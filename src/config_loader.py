from pathlib import Path
import yaml


def load_config():
    config_file = Path("config/config.yaml")

    with open(config_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)