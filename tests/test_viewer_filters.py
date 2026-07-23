import json
import shutil
import subprocess
from pathlib import Path

import pytest


VIEWER_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "simkl_mps"
    / "watch-history-viewer"
    / "script.js"
)


def test_viewer_normalizes_show_type_and_applies_rewatch_filter():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the viewer JavaScript regression test")

    javascript = """
const fs = require('fs');
const vm = require('vm');
const sandbox = { document: { addEventListener() {} } };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox);
const match = sandbox.matchesHistoryTypeAndRewatch;
const item = { type: 'show' };
process.stdout.write(JSON.stringify([
    match(item, 'tv', 'all', { rewatchCount: 0 }),
    match(item, 'tv', 'original', { rewatchCount: 0 }),
    match(item, 'tv', 'original', { rewatchCount: 1 }),
    match(item, 'tv', 'rewatch', { rewatchCount: 1 }),
    match(item, 'movie', 'all', { rewatchCount: 0 })
]));
"""
    result = subprocess.run(
        [node, "-e", javascript, str(VIEWER_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == [True, True, False, True, False]


def test_viewer_rewatch_control_triggers_rendering():
    source = VIEWER_SCRIPT.read_text(encoding="utf-8")

    assert "filterRewatch.addEventListener('change'" in source
