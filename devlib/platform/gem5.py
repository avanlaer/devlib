#    Copyright 2016 ARM Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import re
import subprocess
import sys
import shutil
import time
import types

from devlib.exception import TargetError
from devlib.host import PACKAGE_BIN_DIRECTORY
from devlib.platform import Platform
from devlib.utils.ssh import AndroidGem5Connection, LinuxGem5Connection

class Gem5SimulationPlatform(Platform):

    def __init__(self, name,
                 host_output_dir,
                 gem5_bin,
                 gem5_args,
                 gem5_virtio,
                 core_names=None,
                 core_clusters=None,
                 big_core=None,
                 model=None,
                 modules=None,
                 gem5_telnet_port=None):

        # First call the parent class
        super(Gem5SimulationPlatform, self).__init__(name, core_names, core_clusters,
                                                     big_core, model, modules)

        # Start setting up the gem5 parameters/directories
        # The gem5 subprocess
        self.gem5 = None
        self.gem5_port = gem5_telnet_port or None
        self.stats_directory = host_output_dir
        self.gem5_out_dir = os.path.join(self.stats_directory, "gem5")
        self.gem5_interact_dir = '/tmp' # Host directory
        self.executable_dir = None # Device directory
        self.working_dir = None # Device directory
        self.stdout_file = None
        self.stderr_file = None
        self.stderr_filename = None
        if self.gem5_port is None:
            # Allows devlib to pick up already running simulations
            self.start_gem5_simulation = True
        else:
            self.start_gem5_simulation = False

        # Find the first one that does not exist. Ensures that we do not re-use
        # the directory used by someone else.
        for i in xrange(sys.maxint):
            directory = os.path.join(self.gem5_interact_dir, "wa_{}".format(i))
            try:
                os.stat(directory)
                continue
            except OSError:
                break
        self.gem5_interact_dir = directory
        self.logger.debug("Using {} as the temporary directory."
                          .format(self.gem5_interact_dir))

        # Parameters passed onto gem5
        self.gem5args_binary = gem5_bin
        self.gem5args_args = gem5_args
        self.gem5args_virtio = gem5_virtio
        self._check_gem5_command()

        # Start the interaction with gem5
        self._start_interaction_gem5()

    def _check_gem5_command(self):
        """
        Check if the command to start gem5 makes sense
        """
        if self.gem5args_binary is None:
            raise TargetError('Please specify a gem5 binary.')
        if self.gem5args_args is None:
            raise TargetError('Please specify the arguments passed on to gem5.')
        self.gem5args_virtio = str(self.gem5args_virtio).format(self.gem5_interact_dir)
        if self.gem5args_virtio is None:
            raise TargetError('Please specify arguments needed for virtIO.')

    def _start_interaction_gem5(self):
        """
        Starts the interaction of devlib with gem5.
        """

        # First create the input and output directories for gem5
        if self.start_gem5_simulation:
            # Create the directory to send data to/from gem5 system
            self.logger.info("Creating temporary directory for interaction "
                             " with gem5 via virtIO: {}"
                             .format(self.gem5_interact_dir))
            os.mkdir(self.gem5_interact_dir)

            # Create the directory for gem5 output (stats files etc)
            if not os.path.exists(self.stats_directory):
                os.mkdir(self.stats_directory)
            if os.path.exists(self.gem5_out_dir):
                raise TargetError("The gem5 stats directory {} already "
                                  "exists.".format(self.gem5_out_dir))
            else:
                os.mkdir(self.gem5_out_dir)

            # We need to redirect the standard output and standard error for the
            # gem5 process to a file so that we can debug when things go wrong.
            f = os.path.join(self.gem5_out_dir, 'stdout')
            self.stdout_file = open(f, 'w')
            f = os.path.join(self.gem5_out_dir, 'stderr')
            self.stderr_file = open(f, 'w')
            # We need to keep this so we can check which port to use for the
            # telnet connection.
            self.stderr_filename = f

            # Start gem5 simulation
            self.logger.info("Starting the gem5 simulator")

            command_line = "{} --outdir={} {} {}".format(self.gem5args_binary,
                                                         self.gem5_out_dir,
                                                         self.gem5args_args,
                                                         self.gem5args_virtio)
            self.logger.debug("gem5 command line: {}".format(command_line))
            self.gem5 = subprocess.Popen(command_line.split(),
                                         stdout=self.stdout_file,
                                         stderr=self.stderr_file)

        else:
            # The simulation should already be running
            # Need to dig up the (1) gem5 simulation in question (2) its input
            # and output directories (3) virtio setting
            self._intercept_existing_gem5()

        # As the gem5 simulation is running now or was already running
        # we now need to find out which telnet port it uses
        self._intercept_telnet_port()

    def _intercept_existing_gem5(self):
        """
        Intercept the information about a running gem5 simulation
        e.g. pid, input directory etc
        """
        self.logger("This functionality is not yet implemented")
        raise TargetError()

    def _intercept_telnet_port(self):
        """
        Intercept the telnet port of a running gem5 simulation
        """

        if self.gem5 is None:
            raise TargetError('The platform has no gem5 simulation! '
                              'Something went wrong')
        while self.gem5_port is None:
            # Check that gem5 is running!
            if self.gem5.poll():
                raise TargetError("The gem5 process has crashed with error code {}!".format(self.gem5.poll()))

            # Open the stderr file
            with open(self.stderr_filename, 'r') as f:
                for line in f:
                    m = re.search(r"Listening for system connection on port (?P<port>\d+)", line)
                    if m:
                        port = int(m.group('port'))
                        if port >= 3456 and port < 5900:
                            self.gem5_port = port
                            break
                    # Check if the sockets are not disabled
                    m = re.search(r"Sockets disabled, not accepting terminal connections", line)
                    if m:
                        raise TargetError("The sockets have been disabled!"
                                          "Pass --listener-mode=on to gem5")
                else:
                    time.sleep(1)

    def init_target_connection(self, target):
        """
        Update the type of connection in the target from here
        """
        if target.os == 'linux':
            target.conn_cls = LinuxGem5Connection
        else:
            target.conn_cls = AndroidGem5Connection

    def setup(self, target):
        """
        Deploy m5 if not yet installed
        """
        m5_path = self._deploy_m5(target)
        target.conn.m5_path = m5_path

        # Set the terminal settings for the connection to gem5
        self._resize_shell(target)

    def update_from_target(self, target):
        """
        Set the m5 path and if not yet installed, deploy m5
        Overwrite certain methods in the target that either can be done
        more efficiently by gem5 or don't exist in gem5
        """
        m5_path = target.get_installed('m5')
        if m5_path is None:
            m5_path = self._deploy_m5(target)
        target.conn.m5_path = m5_path

        # Overwrite the following methods (monkey-patching)
        self.logger.debug("Overwriting the 'capture_screen' method in target")
        # Housekeeping to prevent recursion
        setattr(target, 'target_impl_capture_screen', target.capture_screen)
        target.capture_screen = types.MethodType(_overwritten_capture_screen, target)
        self.logger.debug("Overwriting the 'reset' method in target")
        target.reset = types.MethodType(_overwritten_reset, target)
        self.logger.debug("Overwriting the 'reboot' method in target")
        target.reboot = types.MethodType(_overwritten_reboot, target)

        # Call the general update_from_target implementation
        super(Gem5SimulationPlatform, self).update_from_target(target)

    def gem5_capture_screen(self, filepath):
        file_list = os.listdir(self.gem5_out_dir)
        screen_caps = []
        for f in file_list:
            if '.bmp' in f:
                screen_caps.append(f)

        successful_capture = False
        if len(screen_caps) == 1:
            # Bail out if we do not have image, and resort to the slower, built
            # in method.
            try:
                import Image
                gem5_image = os.path.join(self.gem5_out_dir, screen_caps[0])
                temp_image = os.path.join(self.gem5_out_dir, "file.png")
                im = Image.open(gem5_image)
                im.save(temp_image, "PNG")
                shutil.copy(temp_image, filepath)
                os.remove(temp_image)
                gem5_logger.info("capture_screen: using gem5 screencap")
                successful_capture = True

            except (shutil.Error, ImportError, IOError):
                pass

        return successful_capture

    def _deploy_m5(self, target):
        # m5 is not yet installed so install it
        host_executable = os.path.join(PACKAGE_BIN_DIRECTORY,
                                       target.abi, 'm5')
        return target.install(host_executable)

    def _resize_shell(self, target):
        """
        Resize the shell to avoid line wrapping issues.

        """
        # Try and avoid line wrapping as much as possible.
        target.execute('{} stty columns 1024'.format(target.busybox))
        target.execute('reset', check_exit_code=False)

# Methods that will be monkey-patched onto the target
def _overwritten_reset(self):
    raise TargetError('Resetting is not allowed on gem5 platforms!')

def _overwritten_reboot(self):
    raise TargetError('Rebooting is not allowed on gem5 platforms!')

def _overwritten_capture_screen(self, filepath):
    connection_screencapped = self.platform.gem5_capture_screen(filepath)
    if connection_screencapped == False:
        # The connection was not able to capture the screen so use the target
        # implementation
        self.logger.debug('{} was not able to screen cap, using the original target implementation'.format(self.platform.__class__.__name__))
        self.target_impl_capture_screen(filepath)


