"""Test v5.32: nuovi callbacks in command_center_unified.py."""
import sys
sys.path.insert(0, "deploy-agents")


def test_landing_callbacks_in_command_center():
    """command_center ha landing_approve/modify/redo callbacks."""
    with open("deploy/command_center_unified.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert 'landing_approve:' in content, "Manca landing_approve callback"
    assert 'landing_modify:' in content, "Manca landing_modify callback"
    assert 'landing_redo:' in content, "Manca landing_redo callback"
    print("PASS: Landing callbacks presenti")


def test_landing_deploy_callbacks():
    """command_center ha landing_deploy_approve/modify callbacks."""
    with open("deploy/command_center_unified.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert 'landing_deploy_approve:' in content, "Manca landing_deploy_approve"
    assert 'landing_deploy_modify:' in content, "Manca landing_deploy_modify"
    print("PASS: Landing deploy callbacks presenti")


def test_legal_docs_callbacks():
    """command_center ha legal_docs_approve/view/modify callbacks."""
    with open("deploy/command_center_unified.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert 'legal_docs_approve:' in content, "Manca legal_docs_approve"
    assert 'legal_docs_view:' in content, "Manca legal_docs_view"
    assert 'legal_docs_modify:' in content, "Manca legal_docs_modify"
    print("PASS: Legal docs callbacks presenti")


def test_code_cancel_preview_callback():
    """command_center ha code_cancel_preview callback."""
    with open("deploy/command_center_unified.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert 'code_cancel_preview:' in content, "Manca code_cancel_preview"
    assert 'code_new:' in content, "Manca code_new"
    print("PASS: code_cancel_preview + code_new callbacks presenti")


def test_utils_in_dockerfiles():
    """Entrambi i Dockerfile copiano utils/."""
    with open("deploy-agents/Dockerfile", "r", encoding="utf-8") as f:
        agents_df = f.read()
    assert "COPY utils/" in agents_df, "agents Dockerfile manca COPY utils/"

    with open("Dockerfile", "r", encoding="utf-8") as f:
        root_df = f.read()
    assert "utils" in root_df, "root Dockerfile manca utils"
    print("PASS: Entrambi i Dockerfile copiano utils/")


if __name__ == "__main__":
    test_landing_callbacks_in_command_center()
    test_landing_deploy_callbacks()
    test_legal_docs_callbacks()
    test_code_cancel_preview_callback()
    test_utils_in_dockerfiles()
    print("\nTutti i test callbacks v5.32 PASS")
