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

g_adb_tool                  = os.path.join(os.environ['ADB_PATH'], 'adb.exe')
g_android_package           = os.environ['ANDROID_PACKAGE_ID']
g_android_main_activity     = os.environ['MAIN_ACTIVITY']
g_arch_device               = os.environ['ARCH_DEVICE']
g_java_sdk_path             = os.environ['JAVA_SDK_PATH']
g_current_working_path      = os.getcwd()
g_LLDB_working_path         = os.path.join(g_current_working_path, 'LLDB')
#g_android_repository_url    = 'https://dl.google.com/android/repository/'
#g_lldb_tool                 = 'lldb-3.1.4508709-windows.zip'
g_current_miliseconds       = str(int(round(time.time() * 1000)))

def destroy_previous_session_debugger(task):
    command = g_adb_tool + " shell ps"
    proc = subprocess.Popen(command, stdout=subprocess.PIPE)
    output_str, _ = proc.communicate()
    lines = re.split(r'[\r\n]+', output_str.replace("\r", "").rstrip())
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

    PIDS = processes.get(task, [])
    if PIDS:
        print "Destroying previous LLDB server sessions"
        for pid in PIDS:
            print "Killing processes: " + pid
            command = g_adb_tool + " shell run-as " + g_android_package + " kill -9 " + pid 
            subprocess.Popen(command).wait()
    
    return 0


def start_jdb(adb_tool, sdk_path, pid):
    print "Starting jdb to unblock application."

    # Do setup stuff to keep ^C in the parent from killing us.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    
    # Wait until gdbserver has interrupted the program.
    time.sleep(0.5)

    jdb_port = 65534
    command = adb_tool + " -d forward tcp:65534 jdwp:" + pid
    subprocess.Popen(command, stdout=subprocess.PIPE)

    jdb_cmd = os.path.join(sdk_path, 'bin', 'jdb.exe') + " -connect com.sun.jdi.SocketAttach:hostname=localhost,port=65534"
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
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    output, _ = process.communicate()
    lines = re.split(r'[\r\n]+', output.replace("\r", "").rstrip())
    if len(lines) < 2:
        print "Error: device disconnected!"
        return -1
    
    if not "device" in lines[1]:
        print "Error: device disconnected!"
        return -1

    #Check for LLDB tool
    #if not os.path.exists(os.path.join(g_LLDB_working_path, 'bin', 'LLDBFrontend.exe')):
    #    print "LLDB doesn't exists, Downloading Android LLDB tool ... "
    #    LLDB_zip_file = os.path.join(g_current_working_path, g_lldb_tool)
    #    urllib.urlretrieve (g_android_repository_url + g_lldb_tool, LLDB_zip_file)
    #    print "Downloaded!!! , Uncompressing ... "
        
        #Check for LLDB paths
    #    LLDB_path = os.path.join(g_current_working_path, 'LLDB')
    #    if not os.path.exists(LLDB_path):
    #        os.mkdir(LLDB_path)

    #    LLDB_path = os.path.join(LLDB_path, 'Windows')
    #    if not os.path.exists(LLDB_path):
    #        os.mkdir(LLDB_path)

    #    LLDB_zip = zipfile.ZipFile(LLDB_zip_file)
    #    LLDB_zip.extractall(g_LLDB_working_path)
    #    LLDB_zip.close()
    #    print "Downloaded!!! , Uncompressing ... Done"

    destroy_previous_session_debugger("/data/data/" + g_android_package + "/lldb/bin/lldb-server")

    print "Install LLDB files into device"
    
    #Install LLDB Server
    lldb_server_name    = 'lldb-server' 
    lldb_server_path    = os.path.join(g_LLDB_working_path, 'android', g_arch_device, lldb_server_name)
    command = g_adb_tool + ' push ' + lldb_server_path + ' /data/local/tmp/' + lldb_server_name
    subprocess.Popen(command, stdout=subprocess.PIPE).wait()

    #Install LLDB Script
    lldb_server_script  = 'start_lldb_server.sh'
    lldb_server_script_path  = os.path.join(g_LLDB_working_path, 'android', lldb_server_script)
    command = g_adb_tool + ' push ' + lldb_server_script_path + ' /data/local/tmp/' + lldb_server_script
    subprocess.Popen(command, stdout=subprocess.PIPE).wait()

    #Stop Current APP session
    command = g_adb_tool + ' shell am force-stop ' + g_android_package
    subprocess.Popen(command, stdout=subprocess.PIPE).wait()

    #Start Current APP session
    command = g_adb_tool + ' shell am start -n "' + g_android_package + '/' + g_android_main_activity + '" -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -D'
    subprocess.Popen(command, stdout=subprocess.PIPE).wait()

    #Create LLDB folders into device /data/data/<package-id>/lldb and ~/lldb/bin
    command = g_adb_tool + " shell run-as " + g_android_package + " sh -c 'mkdir /data/data/" + g_android_package + "/lldb; mkdir /data/data/" + g_android_package + "/lldb/bin'"
    subprocess.Popen(command, stdout=subprocess.PIPE).wait()

    print "/data/data/" + g_android_package + "/lldb"
    
    #Install lldbserver into package folder /data/data/<package-id>/lldb/bin
    command = g_adb_tool + " shell \"cat /data/local/tmp/lldb-server | run-as " + g_android_package + " sh -c 'cat > /data/data/" + g_android_package + "/lldb/bin/lldb-server && chmod 700 /data/data/" + g_android_package + "/lldb/bin/lldb-server'\""
    subprocess.Popen(command, stdout=subprocess.PIPE).wait()
    
    #Install start_lldb_server.sh script into package folder /data/data/<package-id>/lldb/bin
    command = g_adb_tool + " shell \"cat /data/local/tmp/start_lldb_server.sh | run-as " + g_android_package + " sh -c 'cat > /data/data/" + g_android_package + "/lldb/bin/start_lldb_server.sh && chmod 700 /data/data/" + g_android_package + "/lldb/bin/start_lldb_server.sh'\""
    subprocess.Popen(command, stdout=subprocess.PIPE).wait()
    
    #start start_lldb_server.sh script into package folder /data/data/<package-id>/lldb/bin
    print "Debugger is running ..."
    command = g_adb_tool + " shell run-as " + g_android_package + " sh -c '/data/data/" + g_android_package + "/lldb/bin/start_lldb_server.sh /data/data/" + g_android_package + "/lldb unix-abstract /" + g_android_package + "-0 platform-" + g_current_miliseconds + ".sock \"lldb process:gdb-remote packets\"'"
    debugger_process = subprocess.Popen(command, stdout=subprocess.PIPE)
    
    # Get Current PID for current debugger session
    command = g_adb_tool + " jdwp"
    process_jdwp = subprocess.Popen(command, stdout=subprocess.PIPE)
    #Wait for 1/2 second
    time.sleep(0.5)
    current_pid = process_jdwp.stdout.readline()
    #Kill the current jdwp command
    os.kill(process_jdwp.pid, signal.SIGTERM)

    # Get Current Device's name connected
    command = g_adb_tool + " devices"
    process_device_name = subprocess.Popen(command, stdout=subprocess.PIPE)
    #read dummy first line this is "List of devices attached" string
    process_device_name.stdout.readline()
    device_name = process_device_name.stdout.readline().split()[0]

    #Create script_commands for LLDB
    command_working_lldb = "platform select remote-android\n"
    command_working_lldb += "platform connect unix-abstract-connect://" + device_name + "/" + g_android_package + "-0/platform-" + g_current_miliseconds + ".sock\n"
    command_working_lldb += "settings set auto-confirm true\n"
    command_working_lldb += "settings set plugin.symbol-file.dwarf.comp-dir-symlink-paths /proc/self/cwd\n"
    command_working_lldb += "settings set plugin.jit-loader.gdb.enable-jit-breakpoint true\n"
    command_working_lldb += "command alias fv frame variable\n"
    command_working_lldb += "attach -p " + current_pid + "\n"
#    command_working_lldb += """
#script
#def start_jdb_to_unblock_app():
#  import subprocess
#  subprocess.Popen({})
#start_jdb_to_unblock_app()
#    """.format(repr(
#            [
#                sys.executable,
#                os.path.realpath(__file__),
#                "--wakeup",
#                g_adb_tool,
#                g_java_sdk_path,
#                current_pid,
#            ]))

    #Create Tmp file
    lldb_script_fd, lldb_script_path = tempfile.mkstemp()
    os.write(lldb_script_fd, command_working_lldb)
    os.close(lldb_script_fd)

    lldb_tool_path = os.path.join(g_LLDB_working_path, 'bin', 'lldb.exe')
    #Attach to LLDB
    lldb_process = subprocess.Popen(lldb_tool_path + " -s " + lldb_script_path, creationflags=subprocess.CREATE_NEW_CONSOLE)
    while lldb_process.returncode is None:
        try:
            lldb_process.communicate()
        except KeyboardInterrupt:
            print "haber que paso"
            pass


if __name__ == "__main__":
    main()