"""
advBBS Entry Point

Usage:
    python -m advbbs              # Run BBS server
    python -m advbbs config       # Configuration interface
    python -m advbbs --help       # Show help
"""

import argparse
import sys
import logging
from pathlib import Path

from . import __version__


def setup_logging(level: str, log_file: str | None = None):
    """Configure logging for the application."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers = [logging.StreamHandler()]

    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=log_format,
        handlers=handlers
    )


def main():
    """Main entry point for advBBS."""
    parser = argparse.ArgumentParser(
        prog="advbbs",
        description="advBBS - Lightweight BBS for Meshtastic Mesh Networks"
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"advBBS {__version__}"
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=Path("config.toml"),
        help="Path to configuration file (default: config.toml)"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)"
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Config subcommand
    config_parser = subparsers.add_parser("config", help="Configuration interface")
    config_parser.add_argument("--wizard", action="store_true", help="Run setup wizard")
    config_parser.add_argument(
        "--menu",
        choices=["users", "sync", "meshtastic", "security", "web"],
        help="Jump to specific menu"
    )
    config_parser.add_argument("--show", action="store_true", help="Show current config")
    config_parser.add_argument("--validate", action="store_true", help="Validate config")
    config_parser.add_argument(
        "--set",
        nargs=2,
        metavar=("KEY", "VALUE"),
        help="Set config value"
    )
    config_parser.add_argument("--backup", action="store_true", help="Backup config")
    config_parser.add_argument("--restore", metavar="FILE", help="Restore from backup")

    args = parser.parse_args()

    setup_logging(args.log_level)
    logger = logging.getLogger("advbbs")

    if args.command == "config":
        from .cli.config_rich import ConfigTool
        tool = ConfigTool(args.config if hasattr(args, 'config') else None)
        tool.run()
        sys.exit(0)
    else:
        # Default: run BBS server
        from .core.bbs import advBBS
        from .config import load_config

        try:
            config = load_config(args.config)
            bbs = advBBS(config)
            logger.info(f"Starting advBBS v{__version__}")
            bbs.run()
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Fatal error: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
