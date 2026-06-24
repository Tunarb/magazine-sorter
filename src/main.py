from filename_parser import parse_filename
from path_builder import build_destination
from config_loader import load_config
from scanner import find_magazines


def main():
    config = load_config()

    files = find_magazines(config["input_folder"])

    print(f"Found {len(files)} magazine files")

    for file in files:
        parsed = parse_filename(file.filename)
        destination = build_destination(parsed)

        print()
        print(parsed)
        print(f"Destination: {destination}")


if __name__ == "__main__":
    main()