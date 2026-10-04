import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


DEPLOY = Path(__file__).resolve().parents[1] / "scripts" / "deploy.ps1"
POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None
PWSH = shutil.which("pwsh.exe") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell is needed to check the PC updater")


@pytest.fixture(scope="module")
def mock_openssh(tmp_path_factory):
  directory = tmp_path_factory.mktemp("openssh mocks")
  source = directory / "Stub.cs"
  source.write_text('''using System;
using System.IO;
class Stub {
  static void CopyDirectory(string source, string target) {
    Directory.CreateDirectory(target);
    foreach (string file in Directory.GetFiles(source)) File.Copy(file, Path.Combine(target, Path.GetFileName(file)), true);
    foreach (string dir in Directory.GetDirectories(source)) CopyDirectory(dir, Path.Combine(target, Path.GetFileName(dir)));
  }
  static int Main(string[] args) {
    string tool = Path.GetFileNameWithoutExtension(Environment.GetCommandLineArgs()[0]);
    using (StreamWriter log = File.AppendText(Environment.GetEnvironmentVariable("MOCK_SSH_LOG"))) {
      log.WriteLine("tool=" + tool);
      foreach (string arg in args) log.WriteLine("arg=" + arg);
    }
    if (tool == "scp") {
      if (Environment.GetEnvironmentVariable("MOCK_TRANSFER_ERROR") == "1") return 1;
      CopyDirectory("payload", Environment.GetEnvironmentVariable("MOCK_CAPTURE_PAYLOAD"));
    } else {
      string command = args[args.Length - 1];
      if (command.StartsWith("mktemp ")) {
        if (Environment.GetEnvironmentVariable("MOCK_CONNECT_ERROR") == "1") return 255;
        if (Environment.GetEnvironmentVariable("MOCK_STDERR") == "1") Console.Error.WriteLine("SSH diagnostic on stderr");
        if (Environment.GetEnvironmentVariable("MOCK_BAD_DIRECTORY") == "1") Console.WriteLine("/tmp/../opt");
        else Console.WriteLine("/tmp/cluster-update.Abc12345");
      } else if (command.Contains(" apply ") || command.Contains(" rollback")) {
        if (Environment.GetEnvironmentVariable("MOCK_APPLY_ERROR") == "1") return 1;
        Console.WriteLine("Receiver apply complete");
      }
    }
    return 0;
  }
}''', encoding="utf-8")
  build = directory / "build.ps1"
  build.write_text('''$ErrorActionPreference = 'Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'Stub.cs') -OutputAssembly (Join-Path $PSScriptRoot 'ssh.exe') -OutputType ConsoleApplication
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'ssh.exe') -Destination (Join-Path $PSScriptRoot 'scp.exe')
''', encoding="utf-8")
  result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(build)],
                          capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
  assert result.returncode == 0, result.stdout + result.stderr
  return directory


def _deploy(tmp_path, mock_openssh, *arguments, extra_env=None, powershell=POWERSHELL):
  temp = tmp_path / "temporary files"
  temp.mkdir(exist_ok=True)
  env = dict(os.environ, PATH=str(mock_openssh) + os.pathsep + os.environ["PATH"], TEMP=str(temp), TMP=str(temp),
             MOCK_SSH_LOG=str(tmp_path / "ssh.log"), MOCK_CAPTURE_PAYLOAD=str(tmp_path / "captured payload"))
  env.update(extra_env or {})
  result = subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(DEPLOY),
                           "-PiHost", "orangepizero3w", *arguments],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
  log = tmp_path / "ssh.log"
  return result, log.read_text(encoding="utf-8") if log.exists() else ""


def test_pc_upload_contains_only_runtime_and_checksums(tmp_path, mock_openssh):
  key = tmp_path / "key with spaces"
  key.touch()
  result, log = _deploy(tmp_path, mock_openssh, "-User", "orangepi", "-Port", "2222", "-IdentityFile", str(key))
  assert result.returncode == 0, result.stdout + result.stderr
  assert log.count("tool=ssh\n") == 3
  assert log.count("tool=scp\n") == 1
  assert "arg=-p\narg=2222\narg=-l\narg=orangepi\n" in log
  assert "arg=-P\narg=2222\n" in log
  assert f"arg=-i\narg={key}\n" in log
  assert "arg=orangepi@orangepizero3w:/tmp/cluster-update.Abc12345/" in log
  assert "sudo bash '/tmp/cluster-update.Abc12345/payload/scripts/update.sh' apply" in log
  assert "StrictHostKeyChecking=no" not in log
  payload = tmp_path / "captured payload"
  names = set()
  for line in (payload / "SHA256SUMS").read_text().splitlines():
    expected, name = line.split("  ", 1)
    data = (payload / name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == expected
    assert b"\r\n" not in data
    assert not data.startswith(b"\xef\xbb\xbf")
    names.add(name)
  assert {"cluster_receiver.py", "hdmi_display.py", "scripts/update.sh", "scripts/service.sh"} <= names
  assert not any("tests/" in name or ".venv" in name or name.endswith(".ps1") for name in names)
  assert not list((tmp_path / "temporary files").glob("cluster-deploy-*"))


@pytest.mark.parametrize("failure", ["MOCK_CONNECT_ERROR", "MOCK_TRANSFER_ERROR", "MOCK_BAD_DIRECTORY"])
def test_pc_connection_or_transfer_failure_never_applies_update(tmp_path, mock_openssh, failure):
  result, log = _deploy(tmp_path, mock_openssh, extra_env={failure: "1"})
  assert result.returncode != 0
  assert " apply " not in log
  assert not list((tmp_path / "temporary files").glob("cluster-deploy-*"))
  if failure == "MOCK_TRANSFER_ERROR":
    assert "rm -rf -- '/tmp/cluster-update.Abc12345'" in log
  if failure == "MOCK_BAD_DIRECTORY":
    assert "rm -rf" not in log


def test_pc_apply_failure_is_reported_and_upload_is_cleaned(tmp_path, mock_openssh):
  result, log = _deploy(tmp_path, mock_openssh, extra_env={"MOCK_APPLY_ERROR": "1"})
  assert result.returncode != 0
  assert " apply " in log
  assert "rm -rf -- '/tmp/cluster-update.Abc12345'" in log
  assert not list((tmp_path / "temporary files").glob("cluster-deploy-*"))


def test_pc_rollback_uses_retained_recovery_script_without_upload(tmp_path, mock_openssh):
  result, log = _deploy(tmp_path, mock_openssh, "-Rollback")
  assert result.returncode == 0, result.stdout + result.stderr
  assert log.count("tool=ssh\n") == 1
  assert "tool=scp" not in log
  assert "bash /var/lib/cluster-receiver/updates/update.sh rollback" in log


def test_pc_dry_run_does_not_connect_or_stage_files(tmp_path, mock_openssh):
  result, log = _deploy(tmp_path, mock_openssh, "-DryRun")
  assert result.returncode == 0, result.stdout + result.stderr
  assert not log
  assert "scripts/update.sh" in result.stdout
  assert not list((tmp_path / "temporary files").iterdir())


def test_pc_ssh_stderr_does_not_corrupt_staging_directory(tmp_path, mock_openssh):
  result, log = _deploy(tmp_path, mock_openssh, extra_env={"MOCK_STDERR": "1"})
  assert result.returncode == 0, result.stdout + result.stderr
  assert " apply " in log


@pytest.mark.skipif(not PWSH, reason="PowerShell 7 is not installed")
def test_pc_updater_also_runs_in_powershell_7(tmp_path, mock_openssh):
  result, log = _deploy(tmp_path, mock_openssh, powershell=PWSH)
  assert result.returncode == 0, result.stdout + result.stderr
  assert " apply " in log
  assert "arg=if [ $(id -u) -eq 0 ]; then bash '" in log
