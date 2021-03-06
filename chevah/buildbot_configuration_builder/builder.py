#
# Create buildbot configuration based on a (almost) plain dict.
#
import random

from buildbot.buildslave import BuildSlave
from buildbot.config import BuilderConfig
from buildbot.changes.gitpoller import GitPoller
from buildbot.changes.filter import ChangeFilter
from buildbot.interfaces import IEmailLookup
from buildbot.process.buildstep import BuildStep
from buildbot.process.factory import BuildFactory
from buildbot.process.properties import Property, Interpolate
from buildbot.process.buildrequestdistributor import BasicBuildChooser
from buildbot.schedulers.basic import SingleBranchScheduler
from buildbot.schedulers.triggerable import Triggerable
from buildbot.schedulers.trysched import Try_Userpass
from buildbot.status import html
from buildbot.status.github import GitHubStatus
from buildbot.status.results import FAILURE, SKIPPED, SUCCESS
from buildbot.status.web import authz
from buildbot.status.web.auth import HTPasswdAuth
from buildbot.status.mail import MailNotifier as BuildbotMailNotifier
from buildbot.steps.master import MasterShellCommand
from buildbot.steps.shell import ShellCommand
from buildbot.steps.source.git import Git
from buildbot.steps.transfer import DirectoryUpload
from buildbot.steps.trigger import Trigger
from twisted.internet import defer
from zope.interface import implements

ALL = object()
DEFAULT = object()
DIRECTORY_UPLOAD = 'directory_upload'
GITHUB_PULL_TITLE = object()
INTERESTED_USERS = object()

PARALLEL_GROUP = 'parallel_group'

SEQUENTIAL_GROUP = 'sequential_group'

MASTER_COMMAND = 'master_command'
SLAVE_COMMAND = 'slave_command'
SOURCE_COMMAND = 'source_command'

ATTACH_PNG = 'attach_png'

TRY = object()

POLL_INTERVAL = 60
STABLE_TIMER = 300


@defer.inlineCallbacks
def popNextBuild(self):
    """
    Called when a new build should be created.
    """
    nextBuild = (None, None)

    while True:
        #  2. pick a build
        breq = yield self._getNextUnclaimedBuildRequest()
        if not breq:
            break

        self.bldr.current_builder_request = breq

        #  1. pick a slave
        slave = yield self._popNextSlave()
        if not slave:
            break

        # either satisfy this build or we leave it for another day
        self._removeBuildRequest(breq)

        #  3. make sure slave+ is usable for the breq
        recycledSlaves = []
        while slave:
            canStart = yield self.canStartBuild(slave, breq)
            if canStart:
                break
            # try a different slave
            recycledSlaves.append(slave)
            slave = yield self._popNextSlave(breq)

        # recycle the slaves that we didnt use to the head of the queue
        # this helps ensure we run 'nextSlave' only once per slave choice
        if recycledSlaves:
            self._unpopSlaves(recycledSlaves)

        #  4. done? otherwise we will try another build
        if slave:
            nextBuild = (slave, breq)
            break

    defer.returnValue(nextBuild)

# Patch Buildbot
BasicBuildChooser.popNextBuild = popNextBuild


class UnixCommand(ShellCommand, object):
    """
    Executes a command using an Unix shell.
    """
    haltOnFailure = True

    def __init__(self, **kwargs):
        super(UnixCommand, self).__init__(**kwargs)
        return
        environment = kwargs.get('env', {})
        test_shell = environment.get('TEST_SHELL', None)
        if test_shell:
            # On Window system we call the command with sh.exe.
            self.command.insert(0, test_shell)


class AttachPNG(ShellCommand):
    """
    Attach a PNG image from the slave build folder.

    This is designed to report failures, so by default it will always
    be executed.
    """

    def __init__(self, name, source, alwaysRun=True):
        self._name = name
        self._source = source
        ShellCommand.__init__(
            self,
            command='base64 %s' % (self._source,),
            description='attaching screen',
            descriptionDone='screen attached',
            alwaysRun=alwaysRun,
            )

    def _getLogName(self, name):
        """
        Return a name appropriate for the log name.
        """
        result = name.replace('$', '_')
        result = result.replace('/', '_')
        result = result.replace('\\', '_')
        return result

    def createSummary(self, log):
        image = log.getText().strip()
        if image.startswith('base64:'):
            # Some kind of error while getting the image.
            if 'No such file or directory' in image:
                # No image generated... all good.
                self.finished(SKIPPED)
            else:
                # There is an error.
                self.finished(FAILURE)
            return defer.succeed(None)

        raw_html = '''
<html><body><img src="data:image/png;base64,%s" alt="%s" scale="0">
</body></html>
''' % (image, self._source)
        self.addHTMLLog(self._getLogName(self._source), html=raw_html)
        self.finished(SUCCESS)
        # The first log is the stdio, and we don't need it as we have it in
        # HTML.
        self._step_status.logs.pop(0)
        return defer.succeed(None)


class RunStepsFactory(BuildFactory, object):
    """
    Run commands from 'steps'.
    """

    def __init__(self, project, steps, environment):
        super(RunStepsFactory, self).__init__()

        self._step_environment = environment
        self._project = project
        self._add_steps(steps)

    def _add_steps(self, steps):
        """
        Add all steps from `steps`.
        """
        for step in steps:
            self._add_step(step)

    def _add_step(self, step):
        """
        Add a single step.
        """
        step_type = '_add_step_%s' % (step.get('type', SLAVE_COMMAND))
        try:
            add_step_method = getattr(self, step_type)
        except AttributeError:
            raise AssertionError('Unknown type %s for %s' % (step_type, step))

        add_step_method(step)

    def _add_step_source_command(self, step):
        """
        Add a source step.
        """
        # Use 'incremental' when migrating to latest git step.
        mode = step.get('mode', 'incremental')
        branch = step.get('branch', None)
        config = step.get('config', None)

        self.addStep(Git(
            name='get code for ' + self._project.name,
            mode=mode,
            repourl=self._project.repo,
            branch=branch,
            shallow=False,
            config=config,
            ))

    def _add_step_slave_command(self, step):
        """
        Add a slave command step.
        """
        final_command = step['command'][:]

        name = step.get('name', 'Command')
        optional = step.get('optional', False)
        always_run = step.get('always-run', False)
        timeout = step.get('timeout', 45)

        force_name = 'force_' + name

        # Build environment variables from base environment plus
        # step specific environment variables.
        step_environment = self._step_environment.copy()
        add_environment = step.get('add_environment', {})
        step_environment.update(add_environment)

        done_name = name
        if optional:
            done_name = "%s (prop:force_%s)" % (name, name)

        def do_step_if(step):
            if not optional:
                return True
            return step.build.getProperty(force_name)

        self.addStep(UnixCommand(
            name=name,
            command=final_command,
            doStepIf=do_step_if,
            env=step_environment,
            description=name,
            descriptionDone=done_name,
            alwaysRun=always_run,
            timeout=timeout,
            ))

    def _update_github_status(self, step, set_properties):
        """
        See if the builder should send GitHub Status.
        """
        send_github_status = step.get('github_send_status', False)

        if not send_github_status:
            return

        parts = self._project._github_slug.split('/', 1)
        set_properties.update({
            "github_repo_owner": parts[0],
            "github_repo_name": parts[1],
            })

    def _add_step_sequential_group(self, step):
        """
        Run all builders from group one after another.
        """
        set_properties = step.get('set_properties', {})
        copy_properties = step.get('copy_properties', [])
        self._update_github_status(step, set_properties)

        target_group = step['target']
        for target in self._project.getGroupMembersBuilderNames(target_group):
            step = Trigger(
                schedulerNames=[target],
                waitForFinish=True,
                updateSourceStamp=True,
                set_properties=set_properties,
                copy_properties=copy_properties,
                )
            self.addStep(step)

    def _add_step_parallel_group(self, step):
        """
        Run all builders from group in parallel.
        """
        set_properties = step.get('set_properties', {})
        copy_properties = step.get('copy_properties', [])
        self._update_github_status(step, set_properties)

        target_group = step['target']
        targets = self._project.getGroupMembersBuilderNames(target_group)
        self.addStep(Trigger(
            schedulerNames=targets,
            waitForFinish=True,
            updateSourceStamp=True,
            set_properties=set_properties,
            copy_properties=copy_properties,
            haltOnFailure=True,
            flunkOnFailure=True,
            ))

    def _add_step_master_command(self, step):
        """
        Add a step for master command.
        """
        name = step.get('name', 'Master command')
        always_run = step.get('always-run', False)
        self.addStep(MasterShellCommand(
            name=name,
            command=step['command'],
            haltOnFailure=True,
            alwaysRun=always_run,
            ))

    def _add_step_directory_upload(self, step):
        """
        Add step for directory upload to master.
        """
        name = step.get('name', 'Directory upload')

        optional = step.get('optional', False)
        always_run = step.get('always-run', False)
        force_name = 'force_' + name
        done_name = name
        if optional:
            done_name = "%s (prop:force_%s)" % (name, name)

        def do_step_if(step):
            if not optional:
                return True
            return step.build.getProperty(force_name)

        self.addStep(DirectoryUpload(
            name=done_name,
            slavesrc=step['source'],
            masterdest=step['destination'],
            haltOnFailure=True,
            doStepIf=do_step_if,
            alwaysRun=always_run,
            ))

    def _add_step_attach_png(self, step):
        """
        Attach a PNG file as embedded HTML
        logs.
        """
        name = step.get('name', 'Attach PNG')
        source = step.get('source', 'screenshot.png')
        always_run = step.get('always-run', True)
        self.addStep(AttachPNG(
            name=name,
            source=source,
            alwaysRun=always_run,
            ))


class ParallelFactory(BuildFactory, object):
    """
    Trigger tests in parallel in `target_names`.
    """
    def __init__(self, target_builder_names, steps):
        super(ParallelFactory, self).__init__()

        copy_properties = ['test']
        for step in steps:
            name = step.get('name', None)
            if not name:
                continue
            optional = step.get('optional', False)
            if optional:
                copy_properties.append('force_' + name)

        self.addStep(Trigger(
            schedulerNames=target_builder_names,
            waitForFinish=True,
            updateSourceStamp=True,
            set_properties={},
            copy_properties=copy_properties,
            haltOnFailure=True,
            flunkOnFailure=True,
            ))


def time_delta_hr(start, end):
    """
    Return a string of human readable time delta.
    """
    import datetime
    from dateutil.relativedelta import relativedelta

    start_date = datetime.datetime.fromtimestamp(start)
    end_date = datetime.datetime.fromtimestamp(end)
    delta = relativedelta(end_date, start_date)

    attributes = ['years', 'months', 'days', 'hours', 'minutes', 'seconds']

    result = []
    for attribute_name in attributes:
        attribute = getattr(delta, attribute_name)
        if attribute > 0:
            result.append('%d %s' % (attribute, attribute_name))

    return ', '.join(result)


def message_formatter(mode, name, build, results, master_status):
    """
    Message formater.

    Reasons:
      * try - 'try' job by user
      * 3rd party - Triggerable(server-ubuntu-1004-x64)
      * scheduler

    """
    from buildbot.status.builder import Results

    text = []

    result = Results[results]
    buildbot_url = master_status.getBuildbotURL()
    reason = build.getReason()
    full_logs_url = master_status.getURLForThing(build)
    source_stamp = build.getSourceStamps()[0]
    build_duration = time_delta_hr(*build.getTimes())
    changes = build.getChanges()
    authors = build.getResponsibleUsers()

    properties = []
    for key, value in build.getProperties().properties.items():
        properties.append('%s: %s' % (key, str(value)))

    steps = []
    for step in build.getSteps():
        step_name = "%s - %s " % (step.getName(), ' '.join(step.getText()))
        step_results, dummy = step.getResults()
        try:
            step_status = Results[step_results].upper()
            step_duration = time_delta_hr(*step.getTimes())
        except:
            step_status = 'UNKNOWN'
            step_duration = 'UNKOWN'

        steps.append('')
        steps.append('Status: %s' % step_status)
        steps.append('Step name: %s' % step_name)
        steps.append('Duration: %s' % step_duration)
        for key, value in step.urls.items():
            # The space at the end is important so that the URL are
            # recognized by email clients.
            steps.append('%s: %s ' % (key, str(value)))

    text.append('Branch: %s' % source_stamp.branch)
    text.append('Build status: %s' % result.upper())
    text.append('Authors: %s' % ", ".join(authors))
    text.append('Duration: %s' % build_duration)
    text.append('Full logs: %s' % full_logs_url)
    text.append('Buildslave: %s' % build.getSlavename())
    text.append('Build Reason: %s' % reason)
    text.append('')
    text.append('Steps details')
    text.append('------------------------------------------------------')
    text.extend(steps)
    text.append('')
    text.append('Changes')
    text.append('------------------------------------------------------')
    text.append('')
    text.extend([c.asText() for c in changes])
    text.append('')
    text.append('Build properties')
    text.append('------------------------------------------------------')
    text.extend(properties)
    text.append('')
    text.append('--')
    text.append('Yours truly, Bill Bot.')
    text.append(buildbot_url)
    return {
        'body': "\n".join(text).encode('utf-8'),
        'type': 'plain',
        }


class MailNotifier(BuildbotMailNotifier, object):
    """
    Mail notifier used in project.

    It adds support for sending notification from "try" schedulers.

    mode: change, failing, passing, problem, warnings, exception, all
    """
    def __init__(self,
        server,
        mode,
        builders,
        recipients,
        user_to_email_mapper=None,
        subject=None,
            ):
        kwargs = {}
        kwargs.update(server)
        kwargs.update({
            'messageFormatter': message_formatter,
            'buildSetSummary': False,
            'addPatch': False,
            'addLogs': False,
            'mode': mode,
            'builders': builders,
            'categories': None,  # We use builders.
            })

        kwargs['extraRecipients'] = []
        kwargs['sendToInterestedUsers'] = False
        for name in recipients:
            if name == INTERESTED_USERS:
                kwargs['sendToInterestedUsers'] = True
            else:
                kwargs['extraRecipients'].append(name)

        if subject:
            kwargs['subject'] = subject

        if user_to_email_mapper:
            kwargs['lookup'] = DelegatedLookup(user_to_email_mapper)

        super(MailNotifier, self).__init__(**kwargs)

    def useLookup(self, build):
        """
        For "try" jobs, only send emails to the person requesting the build.

        For rest, send to the list of interested users.
        """
        dl = []

        reason = build.getReason()
        if "'try' job by user" in reason:
            d = defer.maybeDeferred(self.lookup.getAddress, reason)
            dl.append(d)
        else:
            for u in build.getInterestedUsers():
                d = defer.maybeDeferred(self.lookup.getAddress, u)
                dl.append(d)

        return defer.gatherResults(dl)


class DelegatedLookup(object):
    """
    Run a method to get email address.
    """
    implements(IEmailLookup)

    def __init__(self, lookup):
        self._lookup = lookup

    def getAddress(self, user):
        return self._lookup(user)


class ChevahGitPoller(GitPoller):
    """
    Patch upstream poller to reveal poll interval and branch status.
    """

    def describe(self):
        status = ""
        if not self.master:
            status = "[STOPPED - check log]"
        str = ('GitPoller watching at %ss the remote git repository %s, branches: %s last seen %s %s'
                % (self.pollInterval, self.repourl, ', '.join(self.branches), self.lastRev, status, ))
        return str


class ProjectConfiguration(object):
    """
    Generate configuration for a project.
    """

    def __init__(self, project, configuration, parent):
        """
        """
        self._parent = parent
        self._name = project
        self._raw = configuration

        self._repo = self._raw['repo']
        self._github_slug =  self._raw['github_slug']

        self._default = self._raw.get(DEFAULT, {})

    @property
    def name(self):
        return self._name

    @property
    def repo(self):
        return self._repo

    def addProject(self):
        """
        Add project to buildbot_configuration:
        """
        self._all_builder_names = []

        # Create builders after we resolve all group_builder_names.
        for group_name, members in self._raw['groups'].items():
            for member_name in members:

                builder_name = self._getEnvironmentBuilderName(member_name)
                # Don't add the same builder twice.
                if builder_name in self._all_builder_names:
                    continue

                # Create new builder for environment.
                self._all_builder_names.append(builder_name)


                steps = self._getSteps(member_name)
                slaves = self._parent.getSlaves(member_name)
                builder = BuilderConfig(
                    name=builder_name,
                    slavenames=slaves,
                    category=self._name,
                    nextSlave=self._nextSlave,
                    factory=RunStepsFactory(
                        project=self,
                        steps=steps,
                        environment=self._parent.getStepEnvironment(
                            member_name),
                        ),
                    )

                self._parent.addBuilder(builder)

                # self._parent.addNotifications(
                #     builder=builder_name,
                #     configuration={
                #         'email_success'
                #         },
                #     ))

        self._addTryBuilders()
        self._addGateKeepers()

    def _getSteps(self, name):
        """
        Resolve steps.
        """
        steps = self._parent.getDefaultSteps(name)
        custom_steps = self._raw.get('steps', {})
        if custom_steps:
            # We have custom steps for this project.
            # We start by getting the default values for the project,
            # falling back to general default.
            # Then we try to see if there are customs steps, and then
            # fall back to default.
            builder_default = custom_steps.get(DEFAULT, steps)
            steps = custom_steps.get(name, builder_default)

        return steps

    def _addChangeSource(self, project, repo, branches):
        """
        Add a GitPoller.
        """
        if not branches:
            return

        self._parent.addChangeSource(ChevahGitPoller(
            repourl=repo,
            branches=branches,
            project=project,
            category=project,
            pollinterval=POLL_INTERVAL,
            workdir='gitpoller_%s_%s' % (project, '_'.join(branches)),
            ))

    def _addTryBuilders(self):
        """
        Build try schedulers for defined groups.
        """
        for name in self._all_builder_names:
            self._parent.addTryTarget(name)

        for group in self._raw['groups'].keys():

            try:
                target_builder_names = [
                    self._getEnvironmentBuilderName(name)
                    for name in self._raw['groups'][group]]
            except KeyError:
                raise AssertionError(
                    'Failed to enable try group. '
                    'No such group "%s" for project "%s"' % (group, project))

            if not target_builder_names:
                raise AssertionError(
                    'There are no builders in group: %s' % group_builder_name)
            group_builder_name = self._getGroupBuilderName(group)

            self._parent.addTryTarget(group_builder_name)

            steps = self._getSteps(DEFAULT)

            builder = BuilderConfig(
                name=group_builder_name,
                slavenames=self._parent.getTrySlaves(),
                nextSlave=self._nextSlave,
                factory=ParallelFactory(
                    target_builder_names=target_builder_names,
                    steps=steps,
                    ),
                category=self._name,
                )
            self._parent.addBuilder(builder)

    def _getEnvironmentBuilderName(self, name):
        """
        Return a builder name for an individual environment.
        """
        return '%s-%s' % (self._name, name)

    def _getGroupBuilderName(self, name):
        """
        Return builder name for a group.
        """
        return '%s-group-%s' % (self._name, name)

    def getGroupMembersBuilderNames(self, name):
        """
        Return group members for group with `name`.
        """
        result = []
        for member in  self._raw['groups'][name]:
            result.append(self._getEnvironmentBuilderName(member))
        return result

    def _addGateKeepers(self):
        """
        Parse configuration for gatekeepers.
        """
        poll_branches = []

        default = self._parent.getDefaultGateKeeperData()
        default.update(self._default.get('gatekeepers', {}))
        default.update(self._raw['gatekeepers'].get(DEFAULT, {}))

        for keeper, keeper_data in self._raw['gatekeepers'].items():
            if keeper == DEFAULT:
                continue
            data = default.copy()
            data.update(keeper_data)

            scheduler = data['scheduler']
            slaves = self._parent.getSlaves(data['environment'])
            steps = data['steps']
            gatekeeper_properties = {}
            builder_name = '%s-gk-%s' % (self._name, keeper)

            if isinstance(scheduler, basestring):
                poll_branches.append(scheduler)
                stable_timer = data.get('stable_timer', STABLE_TIMER)
                self._parent.addScheduler(SingleBranchScheduler(
                    name=builder_name,
                    change_filter=ChangeFilter(
                        project=self._name,
                        branch=scheduler,
                        ),
                    treeStableTimer=stable_timer,
                    builderNames=[builder_name],
                    ))
            else:
                # Default to TRY scheduler.
                self._parent.addTryTarget(builder_name)

            send_github_status = data.get('github_send_status', False)
            if send_github_status:
                parts = self._github_slug.split('/', 1)
                gatekeeper_properties.update({
                    "github_repo_owner": parts[0],
                    "github_repo_name": parts[1],
                    })

            step_environment= self._parent.getStepEnvironment(
                data['environment'])

            self._parent.addBuilder(BuilderConfig(
                name=builder_name,
                slavenames=slaves,
                nextSlave=self._nextSlave,
                factory=RunStepsFactory(
                    project=self,
                    steps=data['steps'],
                    environment=step_environment,
                    ),
                category=self._name,
                properties=gatekeeper_properties,
                ))

            self._parent.addNotifications(
                builder=builder_name,
                configuration=data.get('notifications', {},
                ))

        self._addChangeSource(
            project=self._name,
            repo=self._repo,
            branches=poll_branches,
            )

    def _nextSlave(self, builder, slaves):
        """
        Controls which slave will be assigned future jobs.
        It is passed two arguments, the Builder object which is assigning a
        new job, and a list of BuildSlave objects.

        The function should return one of the SlaveBuilder objects,
        or None if none of the available slaves should be used.
        """
        request = builder.current_builder_request
        target_name = request.properties.getProperty('target-slave')

        if target_name:
            # See if we have the requested slave.
            for slave_builder in slaves:
                if slave_builder.slave.slavename == target_name:
                    return slave_builder

        for slave_builder in slaves:
            if slave_builder.slave.canStartBuild():
                return slave_builder

        return random.choice(slaves)


class ConfigurationBuilder(object):
    """
    Generated buildmaster configuration.
    """

    def __init__(self, configuration):
        self._raw = configuration

        change_sources_configuration = configuration.get('change_source', {})
        self.poll_interval = change_sources_configuration.get(
            'poll_interval', POLL_INTERVAL)


        self._environments = self._resolveEnvironments()

        self._buildbot = self._raw['global'].copy()
        self._buildbot['status'] = []
        self._buildbot['schedulers'] = []
        self._buildbot['builders'] = []
        self._buildbot['change_source'] = []

        self._buildbot['slaves'] = self._getBuildSlaves()

        self._buildbot['status'].append(self._getWeb())
        self._buildbot['status'].extend(self._getGithHubStatus())

        self._initMailStatus()

        # Builders for which to create try schedulers.
        self._try_targets = []

        self._project_default = self._raw['projects'].get(DEFAULT, {})
        for project, project_configuration in self._raw['projects'].items():
            if project == DEFAULT:
                continue

            project = ProjectConfiguration(
                project, project_configuration, self)
            project.addProject()

        # In the end create try schedulers.
        self._buildbot['schedulers'].extend(self._getTrySchedulers())

    @property
    def environments(self):
        return self._environments

    def getBuidbotConfiguration(self):
        return self._buildbot.copy()

    def addBuilder(self, builder):
        self._buildbot['builders'].append(builder)

    def addChangeSource(self, change):
        self._buildbot['change_source'].append(change)

    def addScheduler(self, scheduler):
        self._buildbot['schedulers'].append(scheduler)

    def addTryTarget(self, target):
        self._try_targets.append(target)

    def getTrySlaves(self):
        """
        Return slaves for running default try builders.
        """
        try_configuration = self._raw.get('try_scheduler', {})
        return self.getSlaves(try_configuration['environment'])

    def getSlaves(self, name):
        """
        Return slaves for environment.
        """
        try:
            return self.environments[name]['slaves']
        except KeyError:
            raise AssertionError('No such environment %s' % name)

    def getDefaultGateKeeperData(self):
        """
        Return default data for gatekeepers
        """
        return self._project_default.get('gatekeepers', {})

    def getDefaultSteps(self, name):
        """
        Return default steps used for all projects.
        """
        all_steps = self._project_default.get('steps', {})
        steps = all_steps.get(name, [])
        if not steps:
            steps = all_steps.get(DEFAULT, [])

        if not steps:
            raise AssertionError('Failed to find the default steps for %s.' % (
                name))

        return steps

    def _getBuildSlaves(self):
        """
        Create slaves.
        """
        slaves = self._raw['slaves']
        result = []
        defaults = slaves[DEFAULT]
        default_password = defaults['password']

        for name, configuration in slaves.items():
            if name == DEFAULT:
                continue

            # Start with default configuration and then overwrite based on
            # slave custom configuration.
            kwargs = defaults.copy()
            kwargs.update(configuration)

            result.append(BuildSlave(name, **kwargs))

        return result

    def _getWeb(self):
        """
        Return a web status based on configuration.
        """
        configuration = self._raw['web']
        http_port = configuration.get('port', 8000)
        htpasswd_path = configuration.get('htpasswd', 'no-such-file')
        authz_kwargs = configuration.get('authorization', {})

        htpasswd_auth = None
        try:
            htpasswd_auth = HTPasswdAuth(htpasswd_path)
        except AssertionError:
            # Don't use authentication if we can not open the file... mainly
            # useful for testing.
            print '!!!WARNING!!! Failed to find the password file.'
            print 'Starting with NO authentication.'
            htpasswd_auth = None

        authz_kwargs['auth'] = htpasswd_auth
        authz_cfg = authz.Authz(**authz_kwargs)

        return html.WebStatus(http_port=http_port, authz=authz_cfg)

    def _getGithHubStatus(self):
        """
        Return a list with configuration for github for empty list if github
        is not enabled.
        """
        configuration = self._raw.get('github', {})
        result = []
        if not configuration:
            return []

        result.append(
            GitHubStatus(
                token=configuration['token'],
                repoOwner=Interpolate("%(prop:github_repo_owner)s"),
                repoName=Interpolate("%(prop:github_repo_name)s"),
                ))
        return result

    def _getTrySchedulers(self):
        """
        Create and return try schedulers and their associated triggerable.
        """
        result = []

        configuration = self._raw['try_scheduler']

        try_scheduler = Try_Userpass(
            name='try',
            port=configuration['port'],
            userpass=configuration['credentials'],
            builderNames=self._try_targets,
            properties={},
            )
        result.append(try_scheduler)

        for name in self._try_targets:
            result.append(Triggerable(name=name, builderNames=[name]))

        return result

    def _resolveEnvironments(self):
        """
        Return a dictionary with resolved values for environments
        """
        configuration = self._raw['environments']

        default = configuration.get(DEFAULT, {})
        result = {}
        for name, data in configuration.items():
            if name == DEFAULT:
                continue
            new_data = default.copy()
            if isinstance(data, list):
                new_data['slaves'] = data
            else:
                new_data.update(data)
            result[name] = new_data

        return result

    def getStepEnvironment(self, name):
        """
        Return resolved environment variables.
        """
        run_environment = self.environments[name].copy()
        run_environment.pop('slaves', None)

        run_environment.update({
            'CI': 'true',
            'BUILDBOT': 'true',
            'COMMIT': Interpolate('%(prop:got_revision)s'),
            'BRANCH': Interpolate('%(prop:branch)s'),
            'BUILD_NUMBER': Interpolate('%(prop:buildnumber)s'),
            'BUILDER_NAME': Interpolate('%(prop:buildername)s'),
            'BUILD_DIR': Interpolate('%(prop:workdir)s'),
            'TEST_ENVIRONMENT': name,
            'TEST_ARGUMENTS': Interpolate('%(prop:test)s'),

            'CODECOV_TOKEN': Interpolate('%(prop:codecov_token)s'),

            'GITHUB_TOKEN': self._raw['github']['token'],
            'GITHUB_PULL_ID': Interpolate('%(prop:github_pull_id)s'),
            'TEST_AUTHOR': Interpolate('%(prop:author)s'),
            })

        return run_environment

    def _initMailStatus(self):
        """
        Initialized mail status configuration
        """
        self._email = self._raw['email']

    def addNotifications(self, builder, configuration):
        """
        Add notifications for builder.
        """
        modes = {
            'email_all': 'all',
            'email_passing': 'passing',
            'email_error': ['failing', 'problem', 'warnings', 'exception'],
            'email_change': ['change']
            }


        supported_types = []
        supported_types.extend(modes.keys())

        for configured_type in configuration.keys():
            if configured_type not in supported_types:
                raise AssertionError(
                    'Unknown notification %s for %s' % (
                        configured_type, builder))

        for email_type, mode in modes.items():
            recipients = configuration.get(email_type, [])

            if not recipients:
                continue

            self._addMailNotification(
                mode=mode, recipients=recipients, builder=builder)


    def _addMailNotification(self, mode, recipients, builder):
        """
        Add email notifications.
        """
        if not recipients:
            raise AssertionError(
                'No recipients for %s on %s.' % (builder, mode))

        subject = self._email.get('subject', None)
        user_to_email_mapper = self._email.get('user_to_email_mapper', None)

        self._buildbot['status'].append(MailNotifier(
            mode=mode,
            server=self._email['server'],
            recipients=recipients,
            builders=[builder],
            user_to_email_mapper=user_to_email_mapper,
            subject=subject,
            ))


def generate_configuration(configuration):
    """
    Return configuration object for build master.
    """
    builder = ConfigurationBuilder(configuration)
    return builder.getBuidbotConfiguration()
