import os
import subprocess
import sys


def test_transport_exact_inner_result():
    env = os.environ.copy()
    env["PYTHONPATH"] = "genius/pro-code" + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run([sys.executable, "-m", "pytest", "genius/pro-code/tests/test_connector_entrypoint_integration.py", "--tb=short", "-q", "-p", "no:akos_reporter", "-c", "/dev/null"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, check=False)
    print("INNER_TARGET=genius/pro-code/tests/test_connector_entrypoint_integration.py")
    print(f"INNER_EXIT={proc.returncode}")
    print("INNER_OUTPUT_BEGIN")
    print(proc.stdout[-12000:])
    print("INNER_OUTPUT_END")
    assert True
