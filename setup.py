"""Bundle the web folder into an installed copy of serve."""

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class build_py_with_web(build_py):
    def run(self) -> None:
        super().run()
        target = Path(self.build_lib) / "serve" / "web"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(Path("web"), target)


setup(cmdclass={"build_py": build_py_with_web})
