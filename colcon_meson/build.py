import os
from pathlib import Path
import shutil
import json
import copy
import difflib

from mesonbuild import coredata
from mesonbuild.mesonmain import CommandLineParser

from colcon_core.environment import create_environment_scripts
from colcon_core.logging import colcon_logger
from colcon_core.shell import get_command_environment
from colcon_core.task import run
from colcon_core.task import TaskExtensionPoint

logger = colcon_logger.getChild(__name__)


def cfg_changed(old, new):
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
    # get changes between old and new configuration
    k_removed = set(old.keys()) - set(new.keys())
    k_added = set(new.keys()) - set(old.keys())
    d_removed = {k: old[k] for k in k_removed}
    d_added = {k: new[k] for k in k_added}
    return d_added, d_removed


class MesonBuildTask(TaskExtensionPoint):
    subparsers = ["setup", "compile", "install"]

    def __init__(self):
        super().__init__()

        self.meson_path = shutil.which("meson")

        self.parsers = dict()
        for subparser in self.subparsers:
            self.parsers[subparser] = CommandLineParser().subparsers.choices[subparser]

    def add_arguments(self, *, parser):
        parser.add_argument('--meson-args',
            nargs='*', metavar='*', type=str.lstrip, default=list(),
            help='Pass arguments to Meson projects.')

    def get_default_config_args(self, args):
        margs = list()

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

    def meson_parse_cmdline(self, cmdline):
        args = dict()
        for module, parser in self.parsers.items():
            # args[module] = parser.parse_known_args(cmdline)[0]
            print(module, "start", cmdline)
            args[module] = parser.parse_args(cmdline)
            args[module], unknown = parser.parse_known_args(cmdline)
            print(module, "start", unknown)
            print(module, "args", args[module])
            module_cmdline = copy.copy(cmdline)
            print("matching...")
            while len(unknown) > 0:
                # print("matching...")
                match = difflib.SequenceMatcher(a=module_cmdline, b=unknown).find_longest_match()
                print("match", match)
                del module_cmdline[match.a:match.a+match.size]
                del unknown[match.b:match.b+match.size]
                print("iter", cmdline)
                print("iter", unknown)
            # module_cmdline = cmdline - unknown
            # module_cmdline = list()
            # for p in cmdline:
            #     if
            # print("kwargs", args[module]._get_kwargs())
            print(module ,"DONE")
        return args

        # # TODO: split "setup" and "compile"
        # print(">>>> cmdline:", cmdline)
        # args = self.parser_setup.parse_known_args(cmdline)[0]
        # print(">>>> setup1 args:", args)
        # args2 = self.parser_compile.parse_known_args(cmdline)[0]
        # # print(">>>> compile1 args:", args2)
        # coredata.parse_cmd_line_options(args)
        # # print(">>>> setup2 args:", args)
        # # coredata.parse_cmd_line_options(args2) # !!!
        # return args

    # def meson_format_cmdline(self, cmdline):
    #     print("cmdline...")
    #     return format_args(self.meson_parse_cmdline(cmdline))
    #     # b = self.meson_parse_cmdline(cmdline)["setup"]
    #     # print("b1", b)
    #     # coredata.parse_cmd_line_options(b)
    #     # print("b2", b)
    #     # a = format_args(b)
    #     # print("a", a)
    #     # return a

    # def meson_format_cmdline_file(self, builddir):
    #     print("file...")
    #     args = self.meson_parse_cmdline([])
    #     coredata.read_cmd_line_file(builddir, args)
    #     return format_args(args)

    async def build(self, *, additional_hooks=None, skip_hook_creation=False,
                    environment_callback=None, additional_targets=None):
        args = self.context.args

        print("$$$$ context args:", args)
        print("$$$$ MESON args:", args.meson_args)

        try:
            env = await get_command_environment('build', args.build_base, self.context.dependencies)
        except RuntimeError as e:
            logger.error(str(e))
            return 1

        if environment_callback is not None:
            environment_callback(env)

        # parse arguments and split per meson module
        module_args = self.meson_parse_cmdline(args.meson_args)

        rc = await self._reconfigure(args, module_args["setup"], env)
        if rc:
            return rc

        rc = await self._build(args, module_args["compile"], env, additional_targets=additional_targets)
        if rc:
            return rc

        rc = await self._install(args, module_args["install"], env)
        if rc:
            return rc

        if not skip_hook_creation:
            create_environment_scripts(self.context.pkg, args, additional_hooks=additional_hooks)

    async def _reconfigure(self, args, module_args, env):
        self.progress('meson')

        print("$$$$ _reconfigure args:", args)
        print("$$$$ _reconfigure module args:", module_args)

        def fmt_config_args(args):
            coredata.parse_cmd_line_options(args)
            return vars(args)

        def fmt_config_cmdline(cmdline):
            args = self.parsers["setup"].parse_args(cmdline)
            coredata.parse_cmd_line_options(args)
            return vars(args)

        def fmt_cmdline_file(builddir):
            args = self.parsers["setup"].parse_args([])
            args = argparse.Namespace()
            print("setup empty args", args)
            coredata.parse_cmd_line_options(args)
            coredata.read_cmd_line_file(builddir, args)
            return vars(args)

        # set default arguments
        cmdline_def = self.get_default_config_args(args)
        # print("cmdline_def args:", cmdline_def)
        # parse default arguments as dict
        # defcfg = self.meson_format_cmdline(cmdline_def)
        defcfg = fmt_config_cmdline(cmdline_def)
        # print("defcfg args:", defcfg)

        buildfile = Path(args.build_base) / "build.ninja"
        configfile = Path(args.build_base) / "meson-info" / "intro-buildoptions.json"

        run_init_setup = not buildfile.exists()

        config_changed = False

        if not run_init_setup:
            # newcfg = self.meson_format_cmdline(args.meson_args)
            newcfg = fmt_config_args(module_args)
            # coredata.parse_cmd_line_options(module_args)
            # newcfg = vars(module_args)
            # print("newcfg", newcfg)
            # oldcfg = self.meson_format_cmdline_file(args.build_base)
            oldcfg = fmt_cmdline_file(args.build_base)
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
            assert(configfile.exists())
            with open(configfile, 'r') as f:
                mesoncfg = {arg["name"]: arg["value"] for arg in json.load(f)}

            # check if command line arguments would change the current meson settings
            config_changed = cfg_changed(mesoncfg, newcfg)
            print("changed?", config_changed)

        if not run_init_setup and not config_changed:
            return

        cmd = list()
        cmd += [self.meson_path]
        cmd += ["setup"]
        cmd.extend(cmdline_def)
        if config_changed:
            logger.info("reconfiguring '{}' because configuration changed".format(self.context.pkg.name))
            cmd += ["--reconfigure"]
        print("module_args", module_args)
        # if module_args:
        #     print("module_args", module_args)
        if args.meson_args:
            cmd += args.meson_args

        completed = await run(self.context, cmd, cwd=args.build_base, env=env, capture_output="stdout")
        if completed.returncode:
            logger.error("\n"+completed.stdout.decode('utf-8'))
        return completed.returncode

    async def _build(self, args, module_args, env, *, additional_targets=None):
        self.progress('build')

        print("$$$$ _build args:", args)

        cmd = list()
        cmd += [self.meson_path]
        cmd += ["compile"]
        # if args.meson_args:
        #     cmd += args.meson_args

        completed = await run(self.context, cmd, cwd=args.build_base, env=env)
        if completed.returncode:
            return completed.returncode

    async def _install(self, args, module_args, env):
        self.progress('install')

        print("$$$$ _install args:", args)

        mesontargetfile = Path(args.build_base) / "meson-info" / "intro-targets.json"
        lastinstalltargetfile = Path(args.build_base) / "last_install_targets.json"

        # get current install targets
        assert(mesontargetfile.exists())
        with open(mesontargetfile, 'r') as f:
            install_targets = {target["name"]:target["install_filename"] for target in json.load(f) if target["installed"]}

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

        cmd = list()
        cmd += [self.meson_path]
        cmd += ["install"]
        # if args.meson_args:
        #     cmd += args.meson_args

        completed = await run(self.context, cmd, cwd=args.build_base, env=env)
        if completed.returncode:
            return completed.returncode


class RosMesonBuildTask(TaskExtensionPoint):
    def __init__(self):
        super().__init__()

    async def build(self):
        meson_extension = MesonBuildTask()
        meson_extension.set_context(context=self.context)
        rc = await meson_extension.build()
        if rc:
            return rc
