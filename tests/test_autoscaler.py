def _run_streaming(cmd, cwd=None, env=None, timeout=300):
    """
    Run a subprocess and stream stdout lines to the console while collecting them.
    Returns (rc, all_output_str).
    """
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError as e:
        return 127, f"executable not found: {cmd[0]!r}. ({e})"
    out_lines = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            # print line immediately
            print(line.rstrip())
            out_lines.append(line)
        proc.wait(timeout=timeout)
        return proc.returncode, "".join(out_lines)
    except subprocess.TimeoutExpired as e:
        proc.kill()
        return 124, f"timeout expired: {e}"
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        return 1, f"error running command: {e}"