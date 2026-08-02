#!/usr/bin/env python3
import re
import os
import subprocess
import sys
import argparse

def convert_octal(match):
    oct_str = match.group(1)
    val = int(oct_str, 8)
    return str(val)

def preprocess_advdat(input_path, output_path):
    with open(input_path, 'r') as f:
        lines = f.readlines()
    formatted = []
    for line in lines:
        if '\t' in line:
            parts = line.rstrip('\r\n').split('\t')
            new_parts = []
            for p in parts:
                try:
                    num = int(p)
                    new_parts.append(f"{num:5d}")
                except ValueError:
                    new_parts.append(p)
            formatted.append("".join(new_parts) + "\n")
        else:
            formatted.append(line)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        f.writelines(formatted)

def preprocess_advf4(input_path, output_path, advdat_formatted_path):
    with open(input_path, 'r') as f:
        lines = f.readlines()

    out_lines = []

    hollerith_map = {
        "'ENTER'": "5HENTER",
        "'STREA'": "5HSTREA",
        "'WATER'": "5HWATER",
        "'WEST'": "4HWEST",
        "'NO'": "2HNO",
        "'N'": "1HN",
        "' '": "1H ",
    }

    posix_dat_path = advdat_formatted_path.replace('\\', '/')

    for line_idx, line in enumerate(lines):
        orig_line = line.rstrip('\r\n')
        
        # Check if comment line
        if len(orig_line) > 0 and orig_line[0] in ['C', 'c', '*']:
            out_lines.append(orig_line)
            continue

        processed = orig_line

        # Replace double-quoted octal literals "1234 -> decimal value
        processed = re.sub(r'"([0-7]+)', convert_octal, processed)

        # Replace TYPE and ACCEPT statements
        m_accept = re.match(r'^(\s*\d*\s*)ACCEPT\s+(\d+)\s*,\s*(.*)$', processed, re.IGNORECASE)
        if m_accept:
            label_prefix = m_accept.group(1)
            fmt_num = m_accept.group(2)
            args = m_accept.group(3)
            processed = f"{label_prefix}READ(*, {fmt_num}) {args}"

        m_type_args = re.match(r'^(\s*\d*\s*)TYPE\s+(\d+)\s*,\s*(.*)$', processed, re.IGNORECASE)
        m_type_noargs = re.match(r'^(\s*\d*\s*)TYPE\s+(\d+)\s*$', processed, re.IGNORECASE)
        if m_type_args:
            label_prefix = m_type_args.group(1)
            fmt_num = m_type_args.group(2)
            args = m_type_args.group(3)
            processed = f"{label_prefix}WRITE(*, {fmt_num}) {args}"
        elif m_type_noargs:
            label_prefix = m_type_noargs.group(1)
            fmt_num = m_type_noargs.group(2)
            processed = f"{label_prefix}WRITE(*, {fmt_num})"

        # Comment out PAUSE statements (legacy PDP-10 debug checkpoints)
        m_pause = re.match(r'^(\s*\d*\s*)PAUSE(.*)$', processed, re.IGNORECASE)
        if m_pause:
            label_prefix = m_pause.group(1)
            processed = f"{label_prefix}CONTINUE"

        # Fix PDP-10 bare G format specifiers using I5
        processed = re.sub(r'FORMAT\s*\(\s*G\s*\)', 'FORMAT(I5)', processed, flags=re.IGNORECASE)
        processed = re.sub(r'FORMAT\s*\(\s*1G\s*,\s*20A5\s*\)', 'FORMAT(I5,20A5)', processed, flags=re.IGNORECASE)
        processed = re.sub(r'FORMAT\s*\(\s*12G\s*\)', 'FORMAT(12I5)', processed, flags=re.IGNORECASE)
        processed = re.sub(r'FORMAT\s*\(\s*G\s*,\s*A5\s*\)', 'FORMAT(I5,A5)', processed, flags=re.IGNORECASE)

        # Replace string literals in expressions (not in FORMAT statements)
        if not re.search(r'\bFORMAT\b', processed, re.IGNORECASE):
            for str_lit, hollerith in hollerith_map.items():
                processed = processed.replace(str_lit, hollerith)

        # Replace RAN(...) with MYRAN(...) to avoid intrinsic conflict
        processed = re.sub(r'\bRAN\b', 'MYRAN', processed)

        # Fix inner loop variable 'I' in SHIFT subroutine (DO 31 I=1,DIST)
        if line_idx >= 720:
            if 'DO 31 I=1,DIST' in processed or 'do 31 i=1,dist' in processed.lower():
                processed = re.sub(r'DO\s+31\s+I\s*=\s*1\s*,\s*DIST', 'DO 31 IDUM=1,DIST', processed, flags=re.IGNORECASE)

        out_lines.append(processed)

    # Add runtime helper subroutines at the end: IFILE and MYRAN
    helpers = f"""
	SUBROUTINE IFILE(N, M)
	IMPLICIT INTEGER(A-Z)
	INTEGER N
	CHARACTER*(*) M
	OPEN(UNIT=N, FILE='{posix_dat_path}', STATUS='OLD')
	END

	REAL FUNCTION MYRAN(QZ)
	IMPLICIT INTEGER(A-Z)
	REAL R
	CALL RANDOM_NUMBER(R)
	MYRAN = R
	END
"""
    out_lines.append(helpers)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        f.write('\n'.join(out_lines))

def compile_target(prep_f, target, output_bin):
    compiler = 'gfortran'
    cmd = []
    if target == 'windows':
        if subprocess.run(['which', 'x86_64-w64-mingw32-gfortran'], capture_output=True).returncode == 0:
            compiler = 'x86_64-w64-mingw32-gfortran'
        elif sys.platform != 'win32':
            print("Warning: x86_64-w64-mingw32-gfortran not found. Falling back to default gfortran.")
        cmd = [
            compiler,
            '-static',
            '-fdec',
            '-fdefault-integer-8',
            '-finit-local-zero',
            '-std=legacy',
            prep_f,
            '-o', output_bin
        ]
    else:
        cmd = [
            compiler,
            '-fdec',
            '-fdefault-integer-8',
            '-finit-local-zero',
            '-std=legacy',
            prep_f,
            '-o', output_bin
        ]

    print(f"Compiling [{target}] with {compiler} -> {output_bin}...")
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print(f"Successfully built '{output_bin}'!")
    else:
        print(f"Compilation failed for target '{target}'.")
        sys.exit(res.returncode)

def main():
    parser = argparse.ArgumentParser(description="Build Colossal Cave Adventure")
    parser.add_argument(
        '--target',
        choices=['native', 'windows', 'all'],
        default='native',
        help="Target platform build choice (default: native)"
    )
    args = parser.parse_args()

    src_f4 = os.path.join('src', 'advf4')
    src_dat = os.path.join('src', 'advdat')
    build_dir = 'build'
    prep_f = os.path.join(build_dir, 'adv_prep.f')
    formatted_dat = os.path.join(build_dir, 'advdat_formatted')

    print("Preprocessing advdat...")
    preprocess_advdat(src_dat, formatted_dat)

    print("Preprocessing advf4...")
    preprocess_advf4(src_f4, prep_f, formatted_dat)

    if args.target == 'native':
        native_bin = 'advent.exe' if sys.platform == 'win32' else 'advent'
        compile_target(prep_f, 'native', native_bin)
    elif args.target == 'windows':
        compile_target(prep_f, 'windows', 'advent.exe')
    elif args.target == 'all':
        native_bin = 'advent.exe' if sys.platform == 'win32' else 'advent'
        compile_target(prep_f, 'native', native_bin)
        compile_target(prep_f, 'windows', 'advent-windows.exe')

if __name__ == '__main__':
    main()
