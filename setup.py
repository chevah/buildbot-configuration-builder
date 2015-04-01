from setuptools import find_packages, setup

VERSION = '0.1.0'

setup(
    name="chevah-buildbot-configuration-builder",
    version=VERSION,
    maintainer='Adi Roiban',
    maintainer_email='adi.roiban@chevah.com',
    license='MIT',
    platforms='any',
    description="Configuration layer on top of Buildbot dict.",
    long_description=open('README.rst').read(),
    url='https://github.com/chevah/buildbot-configuration-builder',
    namespace_packages=['chevah'],
    packages=find_packages('.'),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python",
        ],
    install_requires=[
        'txgithub>=15.0.0',
        'buildbot',
        ],
    )
