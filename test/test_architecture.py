"""测试架构约束 — 验证依赖方向正确"""
import ast
import os
from pathlib import Path


def _collect_imports(directory: str) -> dict[str, set[str]]:
    """收集目录下所有 .py 文件的 import 语句"""
    result = {}
    base = Path(directory)
    for py_file in base.rglob("*.py"):
        rel = str(py_file.relative_to(base.parent.parent))
        imports = set()
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
        result[rel] = imports
    return result


def test_provider_no_telegram_imports():
    """provider 层不应导入 telegram SDK"""
    imports = _collect_imports("biliparser/provider")
    for filepath, modules in imports.items():
        for mod in modules:
            assert "telegram" not in mod.lower() or "bilibili" in mod.lower(), \
                f"{filepath} imports telegram module: {mod}"


def test_model_no_business_imports():
    """model.py 只应导入标准库"""
    tree = ast.parse(Path("biliparser/model.py").read_text())
    allowed_modules = {"dataclasses", "pathlib", "__future__", "typing"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed_modules, \
                    f"model.py imports non-stdlib: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                assert top in allowed_modules, \
                    f"model.py imports non-stdlib: {node.module}"


def test_storage_no_channel_or_provider_imports():
    """storage 层不应导入 channel 或 provider"""
    imports = _collect_imports("biliparser/storage")
    for filepath, modules in imports.items():
        for mod in modules:
            assert "channel" not in mod, \
                f"{filepath} imports channel: {mod}"
            assert "provider" not in mod, \
                f"{filepath} imports provider: {mod}"


def test_no_old_module_imports():
    """确保没有残留的旧模块引用"""
    old_modules = ["biliparser.cache", "biliparser.credentialFactory",
                   "biliparser.database", "biliparser.strategy"]
    for py_file in Path("biliparser").rglob("*.py"):
        content = py_file.read_text()
        for old_mod in old_modules:
            # 检查 from xxx import 和 import xxx 形式
            assert f"from {old_mod}" not in content and f"import {old_mod}" not in content, \
                f"{py_file} still references old module: {old_mod}"
        # 也检查相对导入形式
        assert "from .cache import" not in content or "storage" in str(py_file), \
            f"{py_file} uses old relative import from .cache"
        assert "from .credentialFactory" not in content, \
            f"{py_file} uses old relative import from .credentialFactory"
        assert "from .database" not in content, \
            f"{py_file} uses old relative import from .database"
        assert "from .strategy" not in content, \
            f"{py_file} uses old relative import from .strategy"


def test_old_files_deleted():
    """确保旧文件已被删除"""
    assert not Path("biliparser/cache.py").exists()
    assert not Path("biliparser/credentialFactory.py").exists()
    assert not Path("biliparser/database.py").exists()
    assert not Path("biliparser/strategy").exists()


def test_new_structure_exists():
    """确保新结构的关键文件都存在"""
    required = [
        "biliparser/model.py",
        "biliparser/utils.py",
        "biliparser/__init__.py",
        "biliparser/__main__.py",
        "biliparser/storage/__init__.py",
        "biliparser/storage/cache.py",
        "biliparser/storage/models.py",
        "biliparser/provider/__init__.py",
        "biliparser/provider/bilibili/__init__.py",
        "biliparser/provider/bilibili/api.py",
        "biliparser/provider/bilibili/credential.py",
        "biliparser/provider/bilibili/feed.py",
        "biliparser/provider/bilibili/video.py",
        "biliparser/provider/bilibili/audio.py",
        "biliparser/provider/bilibili/live.py",
        "biliparser/provider/bilibili/opus.py",
        "biliparser/provider/bilibili/read.py",
        "biliparser/channel/__init__.py",
        "biliparser/channel/telegram/__init__.py",
        "biliparser/channel/telegram/bot.py",
        "biliparser/channel/telegram/uploader.py",
    ]
    for f in required:
        assert Path(f).exists(), f"Missing required file: {f}"
