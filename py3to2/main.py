from .convert import *

def convert_path(source_path: str, target_path: str, module_directory: str):
    with open(source_path, 'r', encoding='utf8') as source_f:
        code = source_f.read()

    code = apply_libcst_change(code, target_path, module_directory)
    code = apply_lib3to2_change(code)

    if target_path is None:
        sys.stdout.write(code)
    else:
        with open(target_path, 'w', encoding='utf8') as target_f:
            target_f.write(code)


def initialize_directory(target_dir: str):
    write_base64(BASE64_CONSTS.PY_TYPING, os.path.join(target_dir, '_py3to2_typing.py'))
    write_base64(BASE64_CONSTS.PY_TYPING_EXTENSION, os.path.join(target_dir, '_py3to2_typing_extensions.py'))


def convert(args):
    source_path = args.source
    target_path = args.output
    module_directory = getattr(args, 'module-directory')

    convert_path(source_path, target_path, module_directory)


def convert_all(args):
    directory = args.directory

    for folder, _, files in os.walk(directory):
        for file in files:
            file: str
            if file.endswith('.py'):
                io_path = os.path.join(folder, file)
                convert_path(io_path, io_path, directory)    

    initialize_directory(directory)
    

def write_base64(base64_str, target_path):
    decoded = base64.standard_b64decode(base64_str)
    with open(target_path, 'wb') as f:
        f.write(decoded)


def initialize(args):
    target_dir = args.directory
    initialize_directory(target_dir)


def main():
    parser = argparse.ArgumentParser('py3to2')
    subparsers = parser.add_subparsers(required=True)
    
    parser_convert = subparsers.add_parser('convert')
    parser_convert.add_argument('module-directory', type=str)
    parser_convert.add_argument('source', type=str)
    parser_convert.add_argument('output', type=str)
    parser_convert.set_defaults(func=convert)
    
    parser_convert_all = subparsers.add_parser('convert-all')
    parser_convert_all.add_argument('directory', type=str)
    parser_convert_all.set_defaults(func=convert_all)
    
    parser_initialize = subparsers.add_parser('initialize')
    parser_initialize.set_defaults(func=initialize)
    parser_initialize.add_argument('directory', type=str)

    args = parser.parse_args()
    args.func(args)

