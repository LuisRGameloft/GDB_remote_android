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
import posixpath

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

    print ("Cannot find Program : " + program + " in path = " + path)
    exit()

def run_command(command):
    p = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = p.communicate()
    exit_code = p.returncode
    if exit_code != 0:
        print ("=== Error ===\n") 
        print ("Command : " + command) 
        print ("Output : " + stdout) 
        print ("Output Err : " + stderr) 
        print ("if this error persist, please reboot device")
        exit()

    return stdout, stderr

def get_pid_task(task, adb_tool):
    command = adb_tool + " shell ps"
    stdout, stderr = run_command(command)

    lines = re.split(r'[\r\n]+', stdout.replace("\r", "").rstrip())
    columns = lines.pop(0).split()
    
    try:
        pid_column = columns.index("PID")
    except ValueError:
        pid_column = 1

    processes = dict()
    while lines:
        columns = lines.pop().split()
        process_name = columns[-1]
        pid = columns[pid_column]
        if process_name in processes:
            processes[process_name].append(pid)
        else:
            processes[process_name] = [pid]
    
    return processes.get(task, [])

def destroy_previous_session_debugger(task, adbtool, package):
    command = adbtool + " shell ps"
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
        print ("Destroying previous GDB server sessions")
        for pid in PIDS:
            print ("Killing processes: " + pid)
            command = adbtool + " shell run-as " + package + " kill -9 " + pid
            stdout, stderr = run_command(command)

def start_jdb(adb_tool, jdb_tool, pid):
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
    text_signal = 'print "{}"\n'.format(jdb_magic)
    if sys.version_info[0] == 3:
        text_signal = bytes(text_signal, 'utf-8')

    jdb.stdin.write(text_signal)
    saw_magic_str = False
    while True:
        line = jdb.stdout.readline()
        if sys.version_info[0] == 3:
            line = line.decode("utf-8")

        if line == "":
            break
        #print "jdb output: " + line.rstrip()
        if jdb_magic in line and not saw_magic_str:
            saw_magic_str = True
            time.sleep(0.3)
            exit_signal = "exit\n"
            if sys.version_info[0] == 3:
                exit_signal = bytes(exit_signal, 'utf-8')

            jdb.stdin.write(exit_signal)
    jdb.wait()
    return 0
    
def main():
    if sys.argv[1:2] == ["--wakeup"]:
        return start_jdb(*sys.argv[2:])

    print ("Finding adb tool ...")
    g_adb_tool                  = find_program("adb", os.path.join(os.environ['ADB_PATH'], 'platform-tools'))
    print ("Finding jdb tool ...")
    g_jdb_tool                  = find_program("jdb", os.environ['JAVA_SDK_PATH'])
    print ("Finding gdb tool ...")
    g_ndk_path                  = os.environ['ANDROID_NDK_PATH']
    g_gdb_tool                  = find_program("gdb", g_ndk_path)
    
    g_android_package           = os.environ['ANDROID_PACKAGE_ID']
    g_android_main_activity     = os.environ['MAIN_ACTIVITY']
    g_current_working_path      = os.path.dirname(os.path.realpath(__file__))

    #Check if device is connected
    command = g_adb_tool + " devices"
    stdout, stderr = run_command(command)

    lines = re.split(r'[\r\n]+', stdout.replace("\r", "").rstrip())
    if len(lines) < 2:
        print ("Error: device disconnected!")
        exit()
    
    if not "device" in lines[1]:
        print ("Error: device disconnected!")
        exit()

    #Detect ABI's device
    #Stop Current APP session
    command = g_adb_tool + ' shell getprop ro.product.cpu.abi '
    stdout, stderr = run_command(command)

    #detect ABI
    detectABI = stdout

    #default ABI
    g_arch_device = "arm"
    g_detect_ABI = detectABI.lower().strip()

    #Select ABI 
    if g_detect_ABI == 'arm64-v8a':
        g_arch_device = 'arm64'

    if g_detect_ABI == 'x86':
        g_arch_device = 'x86'

    if g_detect_ABI == 'x86_64':
        g_arch_device = 'x86_64'

    gdb_server_name = "{}-gdbserver".format(g_arch_device)
    
    destroy_previous_session_debugger(gdb_server_name, g_adb_tool, g_android_package)
    
    print ("Getting main libraries to load to debugger ...")
    root_working = os.path.join(g_current_working_path , g_arch_device)
    is_64 = "64" in g_detect_ABI

    required_files = []
    libraries = ["libc.so", "libm.so", "libdl.so"]

    if is_64:
        required_files = ["/system/bin/app_process64", "/system/bin/linker64"]
        library_path = "/system/lib64"
    else:
        required_files = ["/system/bin/linker"]
        library_path = "/system/lib"

    for library in libraries:
        required_files.append(posixpath.join(library_path, library))

    for required_file in required_files:
        # os.path.join not used because joining absolute paths will pick the last one
        local_path = os.path.realpath(root_working + required_file)
        local_dirname = os.path.dirname(local_path)
        if not os.path.isdir(local_dirname):
            os.makedirs(local_dirname)
        
        command = g_adb_tool + ' pull ' + required_file + ' ' + local_path
        stdout, stderr = run_command(command)

    if not is_64:
        destination = os.path.realpath(root_working + "/system/bin/app_process")
        try:
            command = g_adb_tool + ' pull /system/bin/app_process32 ' + destination
            stdout, stderr = run_command(command)
        except:
            command = g_adb_tool + ' pull /system/bin/app_process ' + destination
            stdout, stderr = run_command(command)

    binary_path = os.path.join(root_working, "system", "bin", "app_process")
    if is_64:
        binary_path = os.path.join(root_working, "system", "bin", "app_process64")
    
    print "Install GDB files into device"
    
    #Install GDB Server
    gdb_subfolder_path = "android-{}".format(g_arch_device)
    gdb_server_path = find_program("gdbserver", os.path.join(g_ndk_path, 'prebuilt', gdb_subfolder_path), False)
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
    pids = get_pid_task(g_android_package, g_adb_tool)
    if len(pids) == 0:
        error("Failed to find running process '{}'".format(g_android_package))
    if len(pids) > 1:
        error("Multiple running processes named '{}'".format(g_android_package))
    
    current_pid = pids[0]

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
    if sys.platform.startswith("win"):
        # GDB expects paths to use forward slashes.
        root_working = root_working.replace("\\", "/")
        binary_path = binary_path.replace("\\", "/")

    command_working_gdb = "set osabi GNU/Linux\n"
    command_working_gdb += "file '{}'\n".format(binary_path)
    
    g_solib_search_path = []
    g_solib_search_path.append(root_working)
    g_solib_search_path.append("{}/system/bin".format(root_working))
    if is_64:
        g_solib_search_path.append("{}/system/lib64".format(root_working))
    else:
        g_solib_search_path.append("{}/system/lib".format(root_working))
    g_solib_search_path = os.pathsep.join(g_solib_search_path)
    
    command_working_gdb += "set solib-absolute-prefix {}\n".format(root_working)
    command_working_gdb += "set solib-search-path {}\n".format(g_solib_search_path)
    command_working_gdb += "shell echo Connecting .. \r\n"
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
