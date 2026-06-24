from config_loader import load_config
from scanner import find_magazines


def main():
    config = load_config()

    files = find_magazines(config["input_folder"])

    print(f"Found {len(files)} magazine files")

    for file in files:
        print(f"- {file.filename}")


if __name__ == "__main__":
    main()