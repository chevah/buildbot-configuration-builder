buildbot-configuration-builder
##############################

A configuration layer on top of Buildbot configuration dict.

You pass a simplified dict and it will return a new dict with all objects
required for Buildbot configuration.

This project tries to simplify buildbot configuration by defining a set
of common rules to be used by all builders. It reduced flexibility in favor
or simplicity.

Steps are just shell commands which take arguments and environment variables.

It provide a few specialized steps (ex to get the source code) but is not
designed for custom steps written in Python.

Once the buildbot configuration is defined as a simple dict it should be
easy for any member of the team to maintain it (add slaves or builders).

For configuration the Buildbot you will still need to read buildbot
documentation.

At this stage it is designed to be used for private buildmasters.

It provides the following features:

* Get single
* Multiple projects
* Slaves definition
* Multiple slaves for a builder.
* HTTP web status
* Try schedulers with username/password authentication
* Send build results to GitHub Pull Request state
* Send email notifications

I plan to add:

* Run steps in a Python virtualenv
* All defining Python version for virtualenv
* Scheduler based on GitHub PR commits
* Scheduler based on GitHub commits, to replace polling scheduler.
* IRC status... for example when master builds fail

It does not provide (patches welcomed):

* SVN, BZR, CVS...etc
* Get code from multiple sources


Travis CI comparison
====================

This project was inspired by Travis CI simple configuration file.
I wanted to have something similar for Buildbot.

There are already a few project which can convert a travis.yml into a
buildmaster configuration so this project is not designed this purpose.

I wanted multiple projects on a single Buildbot instance and with slaves
on multiple operating systems.

I also wanted the powers of `buildbot try` which allows to test a patch
without committing the changes.

I also wanted to have separate steps based on each builder, so that the
documentation or static code analysis builders will not have to run the same
steps as for the code test.


Builders
========

The reason why you ended up with buildbot is to get a builder which will
run a set of steps, on a particular slave with option environment variables.

Normal buildbot builder are magically created based on an `environment` and a
set of steps. `environments` are created based on a set of slaves and a set
of environment variables.

Continue reading for info about slaves, environments, projects, steps.


Slaves
======

A slave is the normal buildslave which has a name, password, max_builds.
They a defined using the `slaves` root key.

You have the optional `DEFAULT` slave definition with settings for all slaves.

I hope that slaves are simple to understand, so here is an example::

    {
    OTHER_CONFIGS: {},

    'slaves': {
        # `DEFAULT` values are applied to all slaves and can be overwritten
        # for each slave.
        DEFAULT: {
            'password': 'password',
            'max_builds': 1,
            'notify_on_missing': 'infrastructure@chevah.com',
            },

        # We have 2 linux slaved with default configuration.
        'bs1c-lnx-ubuntu1404-x64-29': {},
        'bs1i-lnx-debian7-x86-37': {},

        # This is a windows slave which uses a different password.
        'bs1i-win-2012-x64-34': {
            'password': 'other-password'
            },

        # We have some linux slaves which can run many tasks.
        'bsmeta1b-lnx-ubuntu1204-x86-30': {'max_builds': 100},
        'bsmeta2i-lnx-ubuntu1204-x86-32': {'max_builds': 100},

        },
    }


Environments
============

The concept of `environment` is inspired by Travis CI environments.

You create an `environment` by associated a set of (similar) slaves with
a set of environment variables.

A slave can be part of multiple environments.

Environments are latest combined with projects and steps to create a builder.

You will define in a single place all the environments used by your projects.
In this way you can share similar environments (builders) between multiple
projects.
Later, in project definition you can choose what environments are associated
with a specific project.

The final builder generated from environment and the project will be named:
`PROJECTNAME-ENVNAME`

All values from the environment dictionary are copied to builders, with
the exception of the `slaves` list.

The builder will run only on one of the slaves associated with the environment,
based on a random rule.

To increase availability you can define multiple slaves for an environment,hence for a builder.

You have the optional `DEFAULT` environment definition with settings for all
environments.

In addition, the following environment values are always set:

* `CI=true`
* `BUILDBOT=true`
* `COMMIT=current_revision`
* `BRANCH=name_of_the_branch`
* `BUILD_NUMBER=buildbot_build_number`
* `BUILDER_NAME=name_of_buildbot_builder`
* `BUILD_DIR=directory_where_test_is_executed`
* `TEST_ENVIRONMENT=name_of_builders_environment`
* `TEST_ARGUMENTS=builder_test_property`

`TEST_ARGUMENTS` are not present in builds triggered by gatekeepers. This is
done on purpose to avoid variable builds for gatekeepers.
`TEST_ARGUMENTS` are copied in group builders, as well as skip steps.

The following environment variables are set if there is a property with the
same name, but in lowercase:

* `GITHUB_TOKEN`
* `GITHUB_PULL_ID`
* `CODECOV_TOKEN`
* `TEST_AUTHOR`


Example::

    {
    OTHER_CONFIGS: {},
    'environments': {
        # These env args are applied to all environments.
        DEFAULT: {
            'TEST_TYPE': 'normal'
            },
        # We want to run tests on Linux
        # but you can create environments based on distro / CPU, etc
        # When combined with project named `brink` it will create a
        # builder named `brink-linux`.
        'linux': {
            'slaves: [
                'bs1c-lnx-ubuntu1404-x64-29',
                'bs1a-lnx-centos7-x64-31'
                ],
            },
        # This will generated a special builder which will overwrite the
        # default environment variable `TEST_TYPE` and also add a new one.
        # For project `brink` it will create builder `brink-leaks`.
        'leaks': {
            'slaves': [
                'bs1c-lnx-ubuntu1404-x64-29',
                'bs1i-win-2012-x64-34',
                ]
            'TEST_TYPE': 'leaks',
            'CHEVAH_GC': 'yes',
            },

        # In case an environment has no extra environment variables you can
        # defined it as a list of slaves.
        # This will create builder `brink-meta-director`.
        'meta-director': [
            'bsmeta1b-lnx-ubuntu1204-x86-30',
            'bsmeta2i-lnx-ubuntu1204-x86-32',
            ],
        },
    }


Projects
========

You can use the same Buildbot installation for multiple projects.
Projects don't have direct access to slaves but rather use the environments
to run project's code on a slave.

A project defines a set of project specific options:

* `repo` - the url used to get project source
* `github_slug` - used to publish GitHub commit status
* `poll_interval` - number of seconds to wait for change source scheduler

It also defines a set of steps, a set of groups and a set of gatekeepers which
are explained later.

A `DEFAULT` project can be used do define default values for all projects.
These values will be used when a project does not specify a specific
configuration.

Here is an example for defining 2 projects. Steps, groups and gatekeepers
are explained later::

    {
    OTHER_CONFIGS: {}
    'projects': {
        DEFAULT: {
            'steps': [DEFAULT_STEPS_FOR_ALL_PROJECTS],

            'poll_interval': 60,
            },
        # `brink` project will use the default steps
        # will will check for changes on master much often.
        'brink': {
            'repo': 'http://git.chevah.com/brink.git',
            'github_slug': 'chevah/brink',
            'poll_interval': 30,
            'groups': BRINK_GROUPS_EXPLAINED_LATER,
            'gatekeepers': BRINK_GK_EXPLAINED_LATER,
            },
        # `compat` project has a different set of steps.
        'compat': {
            'repo': 'http://git.chevah.com/compat.git',
            'github_slug': 'chevah/compat',
            'steps': [COMPAT_SPECIFIC_STEPS],
            'groups': COMPAT_GROUPS_EXPLAINED_LATER,
            'gatekeepers': COMPAT_GK_EXPLAINED_LATER,
            },
        },
    }


Steps
=====

The following step types are available

* SLAVE_COMMAND - execute a shell command on slave
* SOURCE_COMMAND - get project code or apply patch
* MERGE_COMMAND - merge code with a branch

Default step type is SLAVE_COMMAND.

You can conditionally execute a step by using the `optional` configuration.
In this case it will be executed only when when `force_STEPNAME` property
is present on the builder and is not false.

The same set of steps are executed for all builders. In order to run
different tests based on different environments/builders you should dispatch
them bases on environment variable.

For example to run unit tests, pyflakes checker and documentation builder a
single `run_ci` shell command is used which should dispatch a specialized
command based on the environment variables.

Steps are defined inside the project's `steps` key::

    from chevah.buildbot_configuration_builder.builder import (
        MERGE_COMMAND,
        SOURCE_COMMAND,
        )

    {
    OTHER_CONFIGS: {}
    'projects': {
        'brink': {
            'steps': [
                # Get source based on project settings.
                {'type': SOURCE_COMMAND},
                # Merge with master.
                {'type': MERGE_COMMAND , 'branch': 'master'},
                # Option clean the build folder.
                {
                    'name': 'clean',
                    'command': ['bash','./paver.sh', 'clean'],
                    # This step is only executed when `force_clean=1`
                    # builder property is defined.
                    'optional': True,
                    },
                # Build dependencies
                {
                    'name': 'deps',
                    'command': ['bash','./paver.sh', 'deps'],
                    'add_environment': {
                        'SOME_VAR': 'some-value',
                        'OTHER_VAR': Interpolate('%(prop:builder)s'),
                        },
                    },
                # Run tests
                {
                    'name': 'test',
                    'command': ['bash', './paver.sh', 'run_ci'],
                    },
                ],
            },
        },
    }


Groups
======

You can group multiple builders into a group.
For example you can create a group named `pr` which will trigger all builders
required to validate a pull request commit and another group named
`post-commit` which will trigger all builders required to check the committed
code. Or you can group them in 'supported' and 'experimental' builders.

For each member of a group a dedicated builder is created. This builder will
execute the steps associated with this project. The builder will be named
PROJECT_NAME-ENV_NAME.

A builder can be part of multiple groups.

The group will have a dedicated group builder which will trigger a build for
all builders form the group and report the results once all builders are done.

The builder associated with GROUP_NAME for PROJECT_NAME will be named
PROJECT_NAME-group-GROUP_NAME.

Groups are defined inside the project's `groups` key::

    {
    OTHER_CONFIGS: {}
    'projects': {
        'brink': {
            'groups': {
                # This will create a builder named `brink-group-post-commit`
                # and we can use this builder to trigger multiple builders.
                'post-commit': [
                    # Here is a list with environment names, NOT slave names.
                    # This will created the following builders:
                    # brink-leaks, brink-linux-x86, brink-windows-x64
                    'leaks',
                    'linux-x86',
                    'windows-x64'
                    ],
                # This will create a builder named `brink-group-supported`
                # Since `leaks` builder is very slow we don't run to check
                # if a changes is ready for review.
                'supported': [
                    'linux-x86',
                    'windows-x64',
                    ],
                # This will create a group named `brink-group-unstable`.
                'unstable': [
                    'solaris-x86',
                    'freebsd-x64',
                    ],
                },
            },
        },
    }


Gatekeepers
===========

Gatekeepers are specialized builders which will not use the project's steps.

The following step types are available:

* SEQUENTIAL_GROUP - run all builders from a group one after another and wait
  for all builders to end.
* PARALLEL_GROUP - run all builders in parallel and wait for all to end.
* MASTER_COMMAND - run a shell command on master
* DIRECTORY_UPLOAD - upload a folder on master

By default gatekeeper builders are triggered by try schedulers. You can
change this to trigger the builder based on changes on a branch using
`'scheduler': 'master'` option.


Gatekeepers are defined inside the project's `gatekeepers` key::

    {
    OTHER_CONFIGS: {}
    'projects': {
        'brink': {
            'gatekeepers': {
                # Post commit builder which will run all tests
                # in sequential mode.
                'master': {
                    # Run this builder after changes are done on master branch.
                    'scheduler': 'master',
                    'stable_timer': 300,
                    'steps': [
                        {
                        # Trigger each builde from a group, one after another.
                        'type': SEQUENTIAL_GROUP,
                        # Name of the step show in logs.
                        'name': 'step name all',
                        # Name of the triggered group.
                        'target': 'post-commit',
                        },
                        {
                        # Upload local folder `dist` on buildmaster.
                        'type': DIRECTORY_UPLOAD,
                        'name': 'upload_production',
                        'source': 'dist',
                        'destination': '/srv/buildmaster/upload/production',
                        'optional': True,
                        },
                        {
                        # Execute shell command on buildmaster.
                        'type': MASTER_COMMAND,
                        'name': 'Fix permissions',
                        'command': ['chmod', '-R', '755', '/srv/buildmaster/upload'],
                        },
                        ],
                    'notifications': {
                        # Once tests on master are done notify everyone on
                        # both success or failure.
                        'email_all': ['dev@domain.com'],
                        },
                    },

                # Trigger tests and send result to GitHub for pull request
                # status when asked by buildbot try.
                'review': {
                    'steps': [{
                        'type': PARALLEL_GROUP,
                        'name': 'check review step',
                        'target': 'supported',
                        'set_properties': {
                            'codecov_token': '1234',
                            },
                        'copy_properties': ['github_pull_id'],
                        }],
                    'notifications': {
                        'email_all': [INTERESTED_USERS],
                        },
                    'github_send_status': True,
                    },

                # Trigger tests and merge in master on success
                # When asked by buildbot try.
                'merge': {
                    'steps':  [
                        {'type': SOURCE_COMMAND},
                        {
                            'type': PARALLEL_GROUP,
                            'name': 'all',
                            'target': 'all',
                            },
                        {
                            'name': 'merge-commit',
                            'command': ['bash', './paver.sh', 'merge_commit'],
                            },
                        ],
                    'notifications': {
                        'email_passing': ['dev@domain.com'],
                        'email_error': [INTERESTED_USERS],
                        },
                    },
                },
            },
        },
    }


Global buildmaster configuration
================================

You can directly define buildmaster configuration by using the `global` key
from root dict::

    {
    OTHER_CONFIGS: {}
    'global': {
        'title': 'ACME Project Buildbot',
        'db_url': 'sqlite:///state.sqlite',
        'buildbotURL': 'http://build.domain.com/',
        'titleURL': 'http://www.domain.com',

        'slavePortnum': 10089,

        'changeHorizon': 500,
        'buildHorizon': 500,
        'eventHorizon': 50,
        'logHorizon': 50,
        'buildCacheSize': 15,

        'logCompressionMethod': 'gz',

        'mergeRequests': True,
        },
    }


Web status
==========

Web status settings are defined in the `web` key of the root dict::

    {
    OTHER_CONFIGS: {}
    'web': {
        'interface': '0.0.0.0',
        'port': 10088,
        'htpasswd': '/srv/www/buildbot.passwd',
        'authorization': {
            'gracefulShutdown': True,
            'forceBuild': True,
            'forceAllBuilds': True,
            'pingBuilder': True,
            'stopBuild': True,
            'stopAllBuilds': True,
            'cancelPendingBuild': True,
            },
        },
    }


Email notifications
===================

Gatekeeper builders can be configured to send email notifications.

Email settings are defined in the `email` key of the root dict::

    def lookup(user):
        """
        Called with each responsible user of the build and should
        return the full email address.
        """
        return user + '@domain.tld'

    {
    OTHER_CONFIGS: {}
    'email': {
        'server': {
            'fromaddr': 'buildbot@domain.com',
            'relayhost': 'smtp.domain.com',
            'useTls': True,
            'smtpPort': 587,
            'smtpUser': 'stmp-user',
            'smtpPassword': 'stmp-pass',
            },

        # This is optional and use to convert buildbot users to
        # email addresses.
        'user_to_email_mapper': lookup,
        # Available placeholders for subject:
        #  result, projectName, title, builder
        'subject': '%(result)s %(builder)s',
        },
    }


Try schedulers and triggers
===========================

Each builder has an associated Try scheduler so you can use `buildbot try`
to run a patch or a revision.

Group builders and gatekeeper builders also have an associated try scheduler.

They are defined in the `try_scheduler` root key::

    {
    OTHER_CONFIGS: {}
    'try_scheduler': {
        # Port on which to listed for buildbot try requests.
        'port': 10087,
        # Credentials accepted by buildbot try.
        'credentials': [('try_user_name', 'try_user_pass')],
        # Default environment/slaves used to execute the try schedulers for
        # groups and other builders which don't have an explicit environment.
        'environment': 'meta-director',
        },
    }


Slave Selection
===============



GitHub Integration
==================

You can send Buildbot's results for a specific commit to GitHub.
To send them you will need a GitHub token.

Each project also need to define the GitHub slug. See `projects` documentation.

GitHub is configured using the `github` key from root dict::

    {
    OTHER_CONFIGS: {}
    'github': {
        'token': 'GITHUB-TOKEN-VALUE',
        },
    }


master.cfg integration
======================

This is the basic content of your master.cfg file::

    # You will need to import more things for here to define steps.
    from chevah.buildbot_configuration_builder.builder import (
        generate_configuration
        )

    config = {
        'global': {YOUR_DATA},
        'try_scheduler': {YOUR_DATA},
        'github': {YOUR_DATA},
        'web': {YOUR_DATA},
        'email': {YOUR_DATA},
        'slaves': {YOUR_DATA},
        'environments': {YOUR_DATA},
        'projects': {YOUR_DATA},
        }
    BuildmasterConfig = generate_configuration(config)
