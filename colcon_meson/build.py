import os
from pathlib import Path
import shutil
import json

from colcon_core.environment import create_environment_scripts
from colcon_core.logging import colcon_logger
from colcon_core.shell import get_command_environment
from colcon_core.task import run
from colcon_core.task import TaskExtensionPoint

logger = colcon_logger.getChild(__name__)


def parse_install_targets(target_data):
    install_targets = list()
    for t in target_data:
        if t["installed"]:
            install_targets.append(t["name"])
    return install_targets


class MesonBuildTask(TaskExtensionPoint):
    def __init__(self):
        super().__init__()

        self.meson_path = shutil.which("meson")

    async def build(self, *, additional_hooks=None, skip_hook_creation=False,
                    environment_callback=None, additional_targets=None):
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

        cmd = list()
        cmd += [self.meson_path]
        cmd += ["introspect"]
        cmd += [args.build_base]
        cmd += ["--targets"]

        ret = await run(self.context, cmd, cwd=args.path, env=env, capture_output="stdout")

        install_targets = parse_install_targets(json.loads(ret.stdout))

        if install_targets:
            completed = await self._install(args, env)
            if completed.returncode:
                return completed.returncode
        else:
            logger.error("no install targets")

        if not skip_hook_creation:
            create_environment_scripts(self.context.pkg, args, additional_hooks=additional_hooks)

    async def _reconfigure(self, args, env):
        self.progress('meson')

        cache = Path(args.build_base) / "meson-private" / "build.dat"
        run_configure = not cache.exists()
        if not run_configure:
            buildfile = cache.parent / 'build.ninja'
            run_configure = not buildfile.exists()

        if not run_configure:
            return

        cmd = list()
        cmd += [self.meson_path]
        cmd += ["setup"]
        # the LibraryPathEnvironment hook only searches within the fist lib level
        # meson installs by default to "lib/x86_64-linux-gnu"
        cmd += ["--libdir=lib"]
        cmd += ["--prefix=" + args.install_base]
        cmd += [args.build_base] # builddir
        cmd += [args.path] # sourcedir

        completed = await run(self.context, cmd, cwd=args.build_base, env=env)
        return completed.returncode

    async def _build(self, args, env, *, additional_targets=None):
        self.progress('build')

        cmd = list()
        cmd += [self.meson_path]
        cmd += ["compile"]

        completed = await run(self.context, cmd, cwd=args.build_base, env=env)
        if completed.returncode:
            return completed.returncode

    async def _install(self, args, env):
        self.progress('install')

        cmd = list()
        cmd += [self.meson_path]
        cmd += ["install"]

        return await run(self.context, cmd, cwd=args.build_base, env=env)
