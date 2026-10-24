"""Build the wheels that PyPI publishes only as source, into wheels/.

Run by the maintainer (not by setup.ps1), on Windows with Python 3.13 and the
Visual Studio Build Tools ("Desktop development with C++"):

    py -3.13 tools\\build_wheels.py

The C extensions are built against the stable ABI (abi3, Python 3.13 and
later), so one wheel each serves every Python version setup.ps1 accepts.
The sources are the PyPI sdists below, checked by SHA-256 and unmodified;
only the build options differ (Py_LIMITED_API and the abi3 tag).
"""
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "wheels"

SDISTS = [
    # (file name, SHA-256, C extension?)
    ("lzfse-0.4.2.tar.gz", "c6895f8ca13eedd2e18b6e0c987c9468115030898409bc44a7aa8d4f4a42ceab", True),
    ("pylzss-0.3.4.tar.gz", "16818631742488e53a34fda0d402d80edb2b812e11877801e21a9e5ce9b9db1c", True),
    ("hexdump-3.3.zip", "d781a43b0c16ace3f9366aade73e8ad3a7bd5137d58f0b45ab2d3f54876f20db", False),
]

# Runs setup.py with every Extension built for the stable ABI of Python 3.13.
ABI3_DRIVER = r'''
import runpy, sys, setuptools
Base = setuptools.Extension
class Extension(Base):
    def __init__(self, *args, **kwargs):
        kwargs["py_limited_api"] = True
        kwargs["define_macros"] = [*kwargs.get("define_macros", []), ("Py_LIMITED_API", "0x030D0000")]
        super().__init__(*args, **kwargs)
setuptools.Extension = Extension
sys.argv = ["setup.py", "bdist_wheel", "--py-limited-api", "cp313", "-d", sys.argv[1]]
runpy.run_path("setup.py", run_name="__main__")
'''


def vcvars() -> str:
    vswhere = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
    vs = subprocess.run([str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
                        capture_output=True, text=True, check=True).stdout.strip()
    bat = Path(vs) / "VC/Auxiliary/Build/vcvars64.bat"
    if not bat.exists():
        sys.exit("Visual Studio Build Tools (C++) が見つかりません")
    return str(bat)


def fetch(name: str, sha256: str, into: Path) -> Path:
    project = name.rsplit("-", 1)[0]
    url = f"https://files.pythonhosted.org/packages/source/{project[0]}/{project}/{name}"
    data = urllib.request.urlopen(url).read()
    if hashlib.sha256(data).hexdigest() != sha256:
        sys.exit(f"{name}: SHA-256 が一致しません")
    path = into / name
    path.write_bytes(data)
    return path


def unpack(archive: Path, into: Path) -> Path:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(into)
    else:
        with tarfile.open(archive) as t:
            t.extractall(into, filter="data")
    stem = archive.name.removesuffix(".tar.gz").removesuffix(".zip")
    return into / stem if (into / stem).is_dir() else into


def main() -> None:
    if sys.version_info[:2] != (3, 13):
        sys.exit("Python 3.13 で実行してください（abi3 の下限を 3.13 にするため）")
    OUT.mkdir(exist_ok=True)
    bat = vcvars()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        venv = tmp / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        python = venv / "Scripts/python.exe"
        subprocess.run([str(python), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                        "setuptools", "wheel"], check=True)
        driver = tmp / "abi3_setup.py"
        driver.write_text(ABI3_DRIVER, encoding="utf-8")
        for name, sha256, native in SDISTS:
            src = unpack(fetch(name, sha256, tmp), tmp / "src")
            if native:
                # vcvars64.bat fails when PATH has folders with parentheses; give it a minimal one.
                command = (f'set "PATH={os.environ["SystemRoot"]}\\system32;{os.environ["SystemRoot"]}" && '
                           f'call "{bat}" >nul && set DISTUTILS_USE_SDK=1 && '
                           f'"{python}" "{driver}" "{OUT}"')
                # A string, not a list: list quoting escapes the inner quotes for cmd.
                subprocess.run(f'cmd /d /s /c "{command}"', cwd=src, check=True)
            else:
                subprocess.run([str(python), "-m", "pip", "wheel", "-q", "--no-deps", "--no-build-isolation",
                                "-w", str(OUT), str(src)], check=True)
    for wheel in sorted(OUT.glob("*.whl")):
        print(wheel.name, hashlib.sha256(wheel.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
