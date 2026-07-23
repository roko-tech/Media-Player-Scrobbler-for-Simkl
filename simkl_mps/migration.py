"""
User Directory Migration Utility
Migrates user data from 'kavinthangavel' to 'kavin' automatically.
"""

import hashlib
import json
import logging
import os
import pathlib
import shutil
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Migration constants
OLD_USER_SUBDIR = "kavinthangavel"
NEW_USER_SUBDIR = "kavin"
APP_NAME = "simkl-mps"

def get_user_data_paths():
    """Get old and new user data directory paths for current OS."""
    home = pathlib.Path.home()
    
    # Standard paths for all OS
    old_path = home / OLD_USER_SUBDIR / APP_NAME
    new_path = home / NEW_USER_SUBDIR / APP_NAME
    
    return old_path, new_path

MIGRATION_MARKER = ".migration-v2-complete.json"


def _file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files_identical(first, second):
    return first.stat().st_size == second.stat().st_size and _file_digest(first) == _file_digest(second)


def _write_migration_marker(new_path, conflicts):
    marker = new_path / MIGRATION_MARKER
    temp = marker.with_suffix(marker.suffix + ".tmp")
    payload = {
        "schema": 2,
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "conflicts": conflicts,
    }
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(marker)


def migrate_user_directory() -> bool:
    """Merge legacy data per file until old and new locations converge."""
    try:
        old_path, new_path = get_user_data_paths()
        if not old_path.exists():
            logger.debug("Legacy directory does not exist: %s", old_path)
            return True

        new_path.mkdir(parents=True, exist_ok=True)
        conflict_root = new_path / ".migration-conflicts"
        conflicts = []

        source_files = []
        for directory, _, filenames in os.walk(old_path, followlinks=False):
            source_files.extend(Path(directory) / name for name in filenames)

        for source in source_files:
            relative = source.relative_to(old_path)
            destination = new_path / relative

            if destination.is_file() and _files_identical(source, destination):
                source.unlink()
                continue

            if not destination.exists():
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(destination))
                    continue
                except (FileExistsError, NotADirectoryError):
                    pass

            digest = _file_digest(source)[:12]
            conflict = conflict_root / relative.parent / f"{relative.name}.{digest}"
            conflict.parent.mkdir(parents=True, exist_ok=True)
            if conflict.is_file() and _files_identical(source, conflict):
                source.unlink()
            else:
                shutil.move(str(source), str(conflict))
            conflicts.append({
                "source": str(relative),
                "preserved_as": str(conflict.relative_to(new_path)),
            })

        _write_migration_marker(new_path, conflicts)

        for directory, _, _ in os.walk(old_path, topdown=False, followlinks=False):
            try:
                Path(directory).rmdir()
            except OSError:
                pass

        if old_path.exists():
            logger.error("Migration left unresolved entries under %s", old_path)
            return False

        try:
            old_path.parent.rmdir()
        except OSError:
            pass

        if conflicts:
            logger.warning(
                "Migration completed with %s conflict(s) preserved under %s",
                len(conflicts),
                conflict_root,
            )
        else:
            logger.info("Successfully migrated user data to %s", new_path)
        return True
    except Exception as exc:
        logger.error("Failed to migrate user directory: %s", exc, exc_info=True)
        return False

def get_user_subdir() -> str:
    """
    Get the correct user subdirectory, handling migration automatically.
    
    Returns:
        str: The user subdirectory name to use
    """
    # Attempt migration first
    migrate_user_directory()
    
    # Always return the new subdirectory name
    return NEW_USER_SUBDIR

def get_app_data_dir() -> pathlib.Path:
    """
    Get the application data directory, with automatic migration.
    
    Returns:
        pathlib.Path: Path to the app data directory
    """
    home = pathlib.Path.home()
    user_subdir = get_user_subdir()  # This handles migration
    app_data_dir = home / user_subdir / APP_NAME
    
    # Ensure directory exists
    app_data_dir.mkdir(parents=True, exist_ok=True)
    
    return app_data_dir

def migrate_registry_entries():
    """Migrate legacy Windows registry owners into the canonical fork key."""
    if platform.system().lower() != "windows":
        return

    try:
        import winreg

        app_key = r"Media Player Scrobbler for SIMKL"
        legacy_key_paths = (
            rf"Software\kavinthangavel\{app_key}",
            rf"Software\kavin\{app_key}",
        )
        canonical_key_path = rf"Software\roko-tech\{app_key}"

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, canonical_key_path) as target:
            for legacy_path in legacy_key_paths:
                try:
                    legacy = winreg.OpenKey(winreg.HKEY_CURRENT_USER, legacy_path)
                except FileNotFoundError:
                    continue
                with legacy:
                    index = 0
                    while True:
                        try:
                            name, value, value_type = winreg.EnumValue(legacy, index)
                        except OSError:
                            break
                        try:
                            current = winreg.QueryValueEx(target, name)[0]
                        except FileNotFoundError:
                            current = None
                        if current is None or (
                            name == "FirstRun" and int(value or 0) > int(current or 0)
                        ):
                            winreg.SetValueEx(target, name, 0, value_type, value)
                        index += 1
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, legacy_path)
                except OSError:
                    logger.warning("Could not remove legacy registry key %s", legacy_path)
        logger.info("Windows registry settings use the canonical roko-tech owner")
    except ImportError:
        pass
    except Exception as exc:
        logger.error("Failed to migrate registry entries: %s", exc)

def migrate_macos_launch_agents():
    """Migrate macOS Launch Agents if needed."""
    if platform.system().lower() != 'darwin':
        return
        
    try:
        home = pathlib.Path.home()
        launch_agents_dir = home / "Library" / "LaunchAgents"
        
        old_plist = launch_agents_dir / "com.kavinthangavel.simkl-mps.updater.plist"
        new_plist = launch_agents_dir / "com.kavin.simkl-mps.updater.plist"
        
        if old_plist.exists() and not new_plist.exists():
            # Read old plist and update content
            content = old_plist.read_text()
            content = content.replace("kavinthangavel", "kavin")
            
            # Write new plist
            new_plist.write_text(content)
            
            # Remove old plist
            old_plist.unlink()
            
            logger.info("Migrated macOS Launch Agent")
            
    except Exception as e:
        logger.error(f"Failed to migrate macOS Launch Agent: {e}")

def perform_full_migration():
    """Perform complete migration including directories and OS-specific settings."""
    logger.info("Starting user directory migration")
    
    # Migrate main directory
    migrate_user_directory()
    
    # Migrate OS-specific settings
    migrate_registry_entries()
    migrate_macos_launch_agents()
    
    logger.info("Migration completed")
