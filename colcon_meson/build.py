# Copyright 2024 Christian Rauch
# Licensed under the Apache License, Version 2.0

from argparse import ArgumentParser, Namespace
import json
import os
from pathlib import Path
import shutil

# colcon
from colcon_core.environment import create_environment_scripts
from colcon_core.logging import colcon_logger
from colcon_core.shell import get_command_environment
from colcon_core.task import run
from colcon_core.task import TaskExtensionPoint
# meson
from mesonbuild import coredata
from mesonbuild.mesonmain import CommandLineParser

logger = colcon_logger.getChild(__name__)


def cfg_changed(old, new):
    """Compare two configurations and return true if they are equal.

    Args:
        old (dict): old configuration
        new (dict): new configuration

    Returns:
        bool: true if configurations are equal and false otherwise
    """
    for p in old.keys() & new.keys():
        n = new[p]
        # convert string representations of boolen values
        if type(old[p]) is bool and type(n) is str:
            n = bool(n.lower() == "true")
        if n != old[p]:
            logger.debug("option '{}' changed from '{}' to '{}'".format(p, old[p], n))
            return True
    return False


def cfg_diff(old, new):
    """Compare two configurations and return the change.

    Args:
        old (dict): old configuration
        new (dict): new configuration

    Returns:
        (dict, dict): tuple with key-value pairs that were added and remove
                      between the old and new configuration
    """
    # get changes between old and new configuration
    k_removed = set(old.keys()) - set(new.keys())
    k_added = set(new.keys()) - set(old.keys())
    d_removed = {k: old[k] for k in k_removed}
    d_added = {k: new[k] for k in k_added}
    return d_added, d_removed


def format_args(args):
    """Convert Meson command line arguments into key-value pairs.

    Args:
        args: Meson command line arguments

    Returns:
        dict: converted arguments as key-value pairs
    """
    return {arg.name: args.cmd_line_options[arg] for arg in args.cmd_line_options}


class MesonBuildTask(TaskExtensionPoint):
    """Task to build a Meson project."""

    def __init__(self):
        """Initialise the build task by discovering meson and setting up the parser."""
        super().__init__()

        self.meson_path = shutil.which("meson")
        self.parser_setup = CommandLineParser().subparsers.choices["setup"]

    def add_arguments(self, *, parser: ArgumentParser):
        """Add new arguments to the colcon build argument parser.

        Args:
            parser (ArgumentParser): argument parser
        """
        parser.add_argument('--meson-args',
                            nargs='*', metavar='*',
                            type=str.lstrip, default=[],
                            help="Pass 'setup' arguments to Meson projects.",
                            )

    def get_default_args(self, args: Namespace) -> list[str]:
        """Get default Meson arguments.

        Args:
            args (Namespace): parse arguments from an ArgumentParser

        Returns:
            list: list of command line arguments for meson
        """
        margs = []

        # meson installs by default to architecture specific subdirectories,
        # e.g. "lib/x86_64-linux-gnu", but the LibraryPathEnvironment hook
        # only searches within the fist lib level
        margs += ["--libdir=lib"]

        margs += ["--prefix=" + args.install_base]

        # build in release mode by default
        margs += ["--buildtype=release"]

        # positional arguments for 'builddir' and 'sourcedir'
        margs += [args.build_base]
        margs += [args.path]

        return margs

    def meson_parse_cmdline(self, cmdline: list[str]) -> Namespace:
        """Parse command line arguments with the Meson arg parser.

        Args:
            cmdline (list): command line arguments

        Returns:
            Namespace: parse args
        """
        args = self.parser_setup.parse_args(cmdline)
        coredata.parse_cmd_line_options(args)
        return args

    def meson_format_cmdline(self, cmdline: list[str]):
        """Convert Meson args from command line.

        Args:
            cmdline (list): command line arguments

        Returns:
            dict: converted key-value pairs
        """
        return format_args(self.meson_parse_cmdline(cmdline))

    def meson_format_cmdline_file(self, builddir: str):
        """Convert Meson args from command line arguments stored in the build directory.

        Args:
            builddir (str): path to the build directory

        Returns:
            dict: converted key-value pairs
        """
        args = self.meson_parse_cmdline([])
        coredata.read_cmd_line_file(builddir, args)
        return format_args(args)

    async def build(self, *, additional_hooks=None, skip_hook_creation=False,
                    environment_callback=None, additional_targets=None):
        """Full build pipeline for a Meson project.

        Returns:
            int: return code
        """
        args = self.context.args

        try:
            env = await get_command_environment('build', args.build_base, self.context.dependencies)
        except RuntimeError as e:
            logger.error(str(e))
            return 1

        if environment_callback is not None:
            environment_callback(env)

        rc = await self._reconfigure(args, env)
        if rc:
            return rc

        rc = await self._build(args, env, additional_targets=additional_targets)
        if rc:
            return rc

        rc = await self._install(args, env)
        if rc:
            return rc

        if not skip_hook_creation:
            create_environment_scripts(self.context.pkg, args, additional_hooks=additional_hooks)

    async def _reconfigure(self, args, env):
        self.progress('meson')

        # set default arguments
        marg_def = self.get_default_args(args)
        # parse default arguments as dict
        defcfg = self.meson_format_cmdline(marg_def)

        buildfile = Path(args.build_base) / "build.ninja"
        configfile = Path(args.build_base) / "meson-info" / "intro-buildoptions.json"

        run_init_setup = not buildfile.exists()

        config_changed = False

        if not run_init_setup:
            newcfg = self.meson_format_cmdline(args.meson_args)
            oldcfg = self.meson_format_cmdline_file(args.build_base)
            # remove default arguments
            for arg in oldcfg.keys() & defcfg.keys():
                if oldcfg[arg] == defcfg[arg]:
                    del oldcfg[arg]

            # get arguments that are missing from the previous command line
            removed = cfg_diff(oldcfg, newcfg)[1]

            # restore default values if argument was removed
            for arg in removed.keys():
                if arg in defcfg and removed[arg] != defcfg[arg]:
                    newcfg[arg] = defcfg[arg]

            # parse old configuration from meson cache
            assert configfile.exists()
            with open(configfile, 'r') as f:
                mesoncfg = {arg["name"]: arg["value"] for arg in json.load(f)}

            # check if command line arguments would change the current meson settings
            config_changed = cfg_changed(mesoncfg, newcfg)

        if not run_init_setup and not config_changed:
            return

        cmd = []
        cmd += [self.meson_path]
        cmd += ["setup"]
        cmd.extend(marg_def)
        if config_changed:
            logger.info("reconfiguring '{}' because configuration changed".format(self.context.pkg.name))
            cmd += ["--reconfigure"]
        if args.meson_args:
            cmd += args.meson_args

        completed = await run(self.context, cmd, cwd=args.build_base, env=env, capture_output="stdout")
        if completed.returncode:
            logger.error("\n"+completed.stdout.decode('utf-8'))
        return completed.returncode

    async def _build(self, args, env, *, additional_targets=None):
        self.progress('build')

        cmd = []
        cmd += [self.meson_path]
        cmd += ["compile"]

        # append content from the 'MAKEFLAGS' environment variable
        makeflags = env.get("MAKEFLAGS")
        if makeflags:
            cmd.extend(makeflags.split())

        completed = await run(self.context, cmd, cwd=args.build_base, env=env, capture_output="stdout")
        if completed.returncode:
            logger.error("\n"+completed.stdout.decode('utf-8'))
        return completed.returncode

    async def _install(self, args, env):
        self.progress('install')

        mesontargetfile = Path(args.build_base) / "meson-info" / "intro-targets.json"
        lastinstalltargetfile = Path(args.build_base) / "last_install_targets.json"

        # get current install targets
        assert mesontargetfile.exists()
        with open(mesontargetfile, 'r') as f:
            install_targets = {target["name"]: target["install_filename"] for target in json.load(f) if target["installed"]}

        if not install_targets:
            logger.error("no install targets")

        # remove files of removed install targets
        if lastinstalltargetfile.exists():
            with open(lastinstalltargetfile, 'r') as f:
                old_targets = json.load(f)

            removed_targets = set(old_targets.keys()) - set(install_targets.keys())

            if removed_targets:
                logger.info("removing '{}' targets: {}".format(self.context.pkg.name, removed_targets))

            for tgt in removed_targets:
                for path in old_targets[tgt]:
                    if os.path.isfile(path):
                        os.remove(path)
                    elif os.path.isdir(path):
                        shutil.rmtree(path)

        with open(lastinstalltargetfile, 'w') as f:
            json.dump(install_targets, f)

        cmd = []
        cmd += [self.meson_path]
        cmd += ["install"]

        completed = await run(self.context, cmd, cwd=args.build_base, env=env, capture_output="stdout")
        if completed.returncode:
            logger.error("\n"+completed.stdout.decode('utf-8'))
        return completed.returncode


class RosMesonBuildTask(TaskExtensionPoint):
    """Task to build a Meson project."""

    async def build(self):
        """Full build pipeline for a Meson project with a package.xml.

        Returns:
            int: return code
        """
        meson_extension = MesonBuildTask()
        meson_extension.set_context(context=self.context)
        rc = await meson_extension.build()
        if rc:
            return rc
