import os
import subprocess
import os.path
import urllib
import zipfile
import time
import signal
import sys
import multiprocessing
import tempfile
import re

def find_program(program, path, withext=True):
    exts = [""]
    if sys.platform.startswith("win"):
        exts += [".exe", ".bat", ".cmd"]
    
    for x in os.walk(path):
        if os.path.isdir(x[0]):
            if withext:
                for ext in exts:
                    full = x[0] + os.sep + program + ext
                    if os.path.isfile(full):
                        return full
            else:
                full = x[0] + os.sep + program
                if os.path.isfile(full):
                    return full

    print "Cannot find Program : " + program + " in path = " + path
    exit()

if sys.argv[1:2] != ["--wakeup"]:
    print "Finding adb tool ..."
    g_adb_tool                  = find_program("adb", os.environ['ADB_PATH'])
    print "Finding jdb tool ..."
    g_jdb_tool                  = find_program("jdb", os.environ['JAVA_SDK_PATH'])
    print "Finding gdb tool ..."
    g_gdb_tool                  = find_program("gdb", os.environ['ANDROID_NDK_PATH'])
    g_android_package           = os.environ['ANDROID_PACKAGE_ID']
    g_android_main_activity     = os.environ['MAIN_ACTIVITY']


def run_command(command):
    p = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = p.communicate()
    exit_code = p.returncode
    if exit_code != 0:
        print "Command : " + command 
        print "Is not valid" 
        print "if this error persist, please reboot device"
        exit()

    return stdout, stderr

def destroy_previous_session_debugger(task):
    command = g_adb_tool + " shell ps"
    stdout, stderr = run_command(command)

    lines = re.split(r'[\r\n]+', stdout.replace("\r", "").rstrip())
    columns = lines.pop(0).split()
    
    try:
        pid_column = columns.index("PID")
    except ValueError:
        pid_column = 1

    processes = dict()
    PIDS = []
    while lines:
        columns = lines.pop().split()
        process_name = columns[-1]
        pid = columns[pid_column]

        if task in process_name:
            PIDS.append(pid)

    if PIDS:
        print "Destroying previous LLDB server sessions"
        for pid in PIDS:
            print "Killing processes: " + pid
            command = g_adb_tool + " shell run-as " + g_android_package + " kill -9 " + pid
            stdout, stderr = run_command(command)


def start_jdb(adb_tool, jdb_tool, pid):
    print "Starting jdb to unblock application."

    # Do setup stuff to keep ^C in the parent from killing us.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    
    # Wait until gdbserver has interrupted the program.
    time.sleep(0.5)

    command = adb_tool + " -d forward tcp:65534 jdwp:" + pid
    stdout, stderr = run_command(command)

    jdb_cmd = jdb_tool + " -connect com.sun.jdi.SocketAttach:hostname=localhost,port=65534"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    jdb = subprocess.Popen(jdb_cmd,
                           stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT,
                           creationflags=flags)

    # Wait until jdb can communicate with the app. Once it can, the app will
    # start polling for a Java debugger (e.g. every 200ms). We need to wait
    # a while longer then so that the app notices jdb.
    jdb_magic = "__has_started__"
    jdb.stdin.write('print "{}"\n'.format(jdb_magic))
    saw_magic_str = False
    while True:
        line = jdb.stdout.readline()
        if line == "":
            break
        print "jdb output: " + line.rstrip()
        if jdb_magic in line and not saw_magic_str:
            saw_magic_str = True
            time.sleep(0.3)
            jdb.stdin.write("exit\n")
    jdb.wait()
    if saw_magic_str:
        print "JDB finished unblocking application."
    else:
        print "error: did not find magic string in JDB output."

def main():
    if sys.argv[1:2] == ["--wakeup"]:
        return start_jdb(*sys.argv[2:])

    #Check if device is connected
    command = g_adb_tool + " devices"
    stdout, stderr = run_command(command)

    lines = re.split(r'[\r\n]+', stdout.replace("\r", "").rstrip())
    if len(lines) < 2:
        print "Error: device disconnected!"
        exit()
    
    if not "device" in lines[1]:
        print "Error: device disconnected!"
        exit()

    #Detect ABI's device
    #Stop Current APP session
    command = g_adb_tool + ' shell getprop ro.product.cpu.abi '
    stdout, stderr = run_command(command)

    detectABI = stdout

    #default ABI
    g_arch_device = "arm"
    #Select ABI 
    if detectABI.lower() == 'arm64-v8a':
        g_arch_device = 'arm64'

    if detectABI.lower() == 'x86':
        g_arch_device = 'x86'

    if detectABI.lower() == 'x86_64':
        g_arch_device = 'x86_64'

    gdb_server_name = "{}-gdbserver".format(g_arch_device)
    
    destroy_previous_session_debugger(gdb_server_name)
    
    print "Install GDB files into device"
    
    #Install GDB Server
    gdb_subfolder_path = "android-{}".format(g_arch_device)
    gdb_server_path = find_program("gdbserver", os.path.join(os.environ['ANDROID_NDK_PATH'], 'prebuilt', gdb_subfolder_path), False)
    command = g_adb_tool + ' push ' + gdb_server_path + ' /data/local/tmp/' + gdb_server_name
    stdout, stderr = run_command(command)
    
    #Stop Current APP session
    command = g_adb_tool + ' shell am force-stop ' + g_android_package
    stdout, stderr = run_command(command)
    
    #Start Current APP session
    command = g_adb_tool + ' shell am start -n "' + g_android_package + '/' + g_android_main_activity + '" -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -D'
    stdout, stderr = run_command(command)
    
    #Wait for one second
    time.sleep(1)

    # Get Current PID for current debugger session
    command = g_adb_tool + " shell ps | grep " + g_android_package
    stdout, stderr = run_command(command)
    str = stdout
    if len(str) is 0:
        print "Not instance of " + g_android_package + " was found"
        exit()
    current_pid = filter(None, str.split(" "))[1]
    
    #check if exist folder /data/data/<package-id>/gdb 
    command = g_adb_tool + " shell run-as " + g_android_package + " sh -c 'if [ -d \"/data/data/" + g_android_package + "/gdb\" ]; then echo \"1\"; else echo \"0\"; fi;'"
    stdout, stderr = run_command(command)
    if stdout.strip() == '0':
        #Create LLDB folders into device /data/data/<package-id>/gdb 
        command = g_adb_tool + " shell run-as " + g_android_package + " sh -c 'mkdir /data/data/" + g_android_package + "/gdb'"
        stdout, stderr = run_command(command)

    #check if exist folder /data/data/<package-id>/gdb/bin
    command = g_adb_tool + " shell run-as " + g_android_package + " sh -c 'if [ -d \"/data/data/" + g_android_package + "/gdb/bin\" ]; then echo \"1\"; else echo \"0\"; fi;'"
    stdout, stderr = run_command(command)
    if stdout.strip() == '0':
        #Create LLDB folders into device /data/data/<package-id>/gdb/bin
        command = g_adb_tool + " shell run-as " + g_android_package + " sh -c 'mkdir /data/data/" + g_android_package + "/gdb/bin'"
        stdout, stderr = run_command(command)

    #Install gdbserver into package folder /data/data/<package-id>/gdb/bin
    command = g_adb_tool + " shell \"cat /data/local/tmp/" + gdb_server_name + " | run-as " + g_android_package + " sh -c 'cat > /data/data/" + g_android_package + "/gdb/bin/" + gdb_server_name + " && chmod 700 /data/data/" + g_android_package + "/gdb/bin/" + gdb_server_name + "'\""
    stdout, stderr = run_command(command)
    
    #start gbserver into package folder /data/data/<package-id>/gdb/bin
    print "Debugger is running ..."
    command = g_adb_tool + " shell \"run-as " + g_android_package + " sh -c '/data/data/" + g_android_package + "/gdb/bin/" + gdb_server_name + " :5039 --attach " + current_pid  + "'\""
    subprocess.Popen(command, stdout=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_CONSOLE)

    #Forward port 
    command = g_adb_tool + " forward tcp:5039 tcp:5039"
    stdout, stderr = run_command(command)
    
    #Create script_commands for LLDB
    command_working_gdb = "set osabi GNU/Linux\n"
    command_working_gdb +="shell echo Connecting .. \r\n"
    command_working_gdb += """
python

def target_remote_with_retry(target, timeout_seconds):
  import time
  end_time = time.time() + timeout_seconds
  while True:
    try:
      gdb.execute('target remote ' + target)
      return True
    except gdb.error as e:
      time_left = end_time - time.time()
      if time_left < 0 or time_left > timeout_seconds:
        print("Error: unable to connect to device.")
        print(e)
        return False
      time.sleep(min(0.25, time_left))

target_remote_with_retry(':{}', {})

end
""".format(5039, 5)    

    command_working_gdb += """
python
def start_jdb_to_unblock_app():
  import subprocess
  subprocess.Popen({})

start_jdb_to_unblock_app()

end
""".format(repr(
            [
                "python",
                os.path.realpath(__file__),
                "--wakeup",
                g_adb_tool,
                g_jdb_tool,
                current_pid,
            ]))
    
    command_working_gdb +="continue \r\n"

    #Create Tmp file
    gdb_script_fd, gdb_script_path = tempfile.mkstemp()
    os.write(gdb_script_fd, command_working_gdb)
    os.close(gdb_script_fd)

    #Attach to GDB
    gbd_process = subprocess.Popen(g_gdb_tool + " -x " + gdb_script_path, creationflags=subprocess.CREATE_NEW_CONSOLE)
    while gbd_process.returncode is None:
        try:
            gbd_process.communicate()
        except KeyboardInterrupt:
            pass
        
    os.unlink(gdb_script_path)

if __name__ == "__main__":
    main()