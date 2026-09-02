"""The one-time GCP setup, runnable from an installed package.

The script this wraps is the only part of setup that is not a library call, and
it used to live in the repository -- which meant the quickstart opened with a
``git clone`` performed solely to obtain one file. Shipping it as package data
and exec'ing it here keeps ``uvx platform-mcp`` the only thing anyone installs.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from importlib import resources

SCRIPT_NAME = "setup-service-account.sh"


def run_setup(args: list[str]) -> int:
    """Run the service-account setup script, passing ``args`` straight through.

    Returns its exit status, so a failed grant fails the command.
    """
    bash = shutil.which("bash")
    if bash is None:
        print(
            "error: bash not found on PATH. The setup script is bash; run it "
            "from a shell that has it, or follow the manual steps in the README.",
            file=sys.stderr,
        )
        return 1
    if shutil.which("gcloud") is None:
        print(
            "error: the gcloud CLI is not on PATH. Install it first: "
            "https://cloud.google.com/sdk/docs/install",
            file=sys.stderr,
        )
        return 1

    script = resources.files(__package__).joinpath("scripts", SCRIPT_NAME)
    # as_file materializes the script when the package is zipped, and the
    # explicit bash invocation means the wheel need not preserve the mode bits.
    with resources.as_file(script) as path:
        return subprocess.call([bash, str(path), *args])
