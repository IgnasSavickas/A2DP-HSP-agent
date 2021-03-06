#!/usr/bin/python

from __future__ import absolute_import, print_function, unicode_literals

import sys
import dbus
import dbus.service
import dbus.mainloop.glib
import os.path
import subprocess
import time

try:
  from gi.repository import GObject
except ImportError:
  import gobject as GObject

AGENT_INTERFACE = "org.bluez.Agent1"
HANDS_FREE_INTERFACE = "org.ofono.Handsfree"
AGENT_PATH = "/test/agent"
FILE_DIR_PATH = "/var/local/"
DEVICES_FILE_PATH = FILE_DIR_PATH + "devices.txt"
A2DP_UUID = "0000110d-0000-1000-8000-00805f9b34fb"
HFP_UUID = "0000111e-0000-1000-8000-00805f9b34fb"

bus = None
connected = True
modem_path = None
a2dp_connected = False
hfp_connected = False
inband_ringing = False
incomming_call = None

class Rejected(dbus.DBusException):
	_dbus_error_name = "org.bluez.Error.Rejected"

class Agent(dbus.service.Object):
	exit_on_release = True

	def set_exit_on_release(self, exit_on_release):
		self.exit_on_release = exit_on_release

	@dbus.service.method(AGENT_INTERFACE,
					in_signature="", out_signature="")
	def Release(self):
		print("Release")
		if self.exit_on_release:
			mainloop.quit()

	@dbus.service.method(AGENT_INTERFACE,
					in_signature="os", out_signature="")
	def AuthorizeService(self, device, uuid):
		global connected
		print("AuthorizeService (%s, %s)" % (device, uuid))
		if uuid == A2DP_UUID and not a2dp_connected:
			print("Authorized A2DP Service")
			add_device(device)
			return
		elif uuid == HFP_UUID and not hfp_connected:
			print("Authorized HFP Service")
			add_device(device)
			return
		print("Rejecting Service")
		raise Rejected("Connection rejected")

def add_device(new_device):
    try:
        file_read = open(DEVICES_FILE_PATH, "r")
        devices = file_read.read().splitlines()
        file_read.close()
        if new_device in devices: devices.remove(new_device)
        else: print("New device added (%s)" % new_device)
        devices.insert(0, new_device)
        if (len(devices) > 6): del devices[6:len(devices)]
        file_write = open(DEVICES_FILE_PATH, "w")
        file_write.write('\n'.join(devices) + '\n')
        file_write.close()
    except Exception:
        file = open(DEVICES_FILE_PATH, "w")
        file.write(new_device + '\n')
        print("New device added (%s)" % new_device)
        file.close()

def dev_connect(path):
    dev = dbus.Interface(bus.get_object("org.bluez", path),
                            "org.bluez.Device1")
    try:
        dev.Connect()
        print("Connected (%s)" % path)
        global connected
        connected = False
        add_device(path)
        return True
    except Exception:
        return False

def connect_to_dev():
    try:
        file = open(DEVICES_FILE_PATH, "r")
        dev_paths = file.read().splitlines()
        print("Connecting to recent devices...")
        for path in dev_paths:
            if dev_connect(path): return
        print("No devices are available. Waiting for connection...")
        global connected
        connected = False
        file.close()
    except Exception:
        print("No recent devices to connect. Waiting for connection...")
        return

def play_connected_sound():
	global connected
	if not connected:
		connected = True
		subprocess.call(["aplay", FILE_DIR_PATH + "connected.wav"])

def play_disconnect_sound():
	if not (a2dp_connected or hfp_connected):
		global connected
		connected = False
		subprocess.call(["aplay", FILE_DIR_PATH + "disconnected.wav"])

def play_battery_sound():
	subprocess.call(["aplay", FILE_DIR_PATH + "battery.wav"])

def player_changed(interface, properties, invalidated):
	if interface == "org.bluez.MediaControl1" and 'Connected' in properties:
		global a2dp_connected
		if properties['Connected'] and not a2dp_connected:
			print("A2DP Connected")
			a2dp_connected = True
			play_connected_sound()
		elif not properties['Connected'] and a2dp_connected:
			print("A2DP Disconnected")
			a2dp_connected = False
			play_disconnect_sound()

def hands_free_changed(property, value):
	if property == "BatteryChargeLevel":
		battery_level = int(value)
		print("Battery level changed:", battery_level)
		if int(value) == 0: play_battery_sound()

def modem_changed(property, value):
	if property == "Powered":
		global hfp_connected, incomming_call
		if value and not hfp_connected:
			print("HSP Connected")
			hfp_connected = True
			play_connected_sound()
			manager = dbus.Interface(bus.get_object('org.ofono', '/'),
							'org.ofono.Manager')
			modems = manager.GetModems()
			for modem in modems:
				if modem[1]["Powered"]:
					modem_path = modem[0]
					get_hfp_properties(modem_path)
					break
		elif not value and hfp_connected:
			print("HSP Disconnected")
			hfp_connected = False
			if incomming_call:
				incomming_call.terminate()
				incomming_call = None
			modem_path = None
			play_disconnect_sound()

def voice_call_changed(property, value):
	global incomming_call
	print("Call %s:" % property, value)
	if property == "State" and value != "incoming" and incomming_call:
		incomming_call.terminate()
		incomming_call = None

def voice_call_added(path, properties):
	global incomming_call
	print("Call added (%s):" % path, properties['State'])
	if properties['State'] == "incoming" and incomming_call is None and not inband_ringing:
		incomming_call = subprocess.Popen(["aplay", FILE_DIR_PATH + "calling.wav"])

def voice_call_removed(path):
	global incomming_call
	print("Call removed (%s)" % path)
	if incomming_call:
		incomming_call.terminate()
		incomming_call = None

def add_signal_receivers():
	bus.add_signal_receiver(player_changed,
		dbus_interface = "org.freedesktop.DBus.Properties",
		signal_name = "PropertiesChanged")
	bus.add_signal_receiver(hands_free_changed,
		dbus_interface = HANDS_FREE_INTERFACE,
		signal_name = "PropertyChanged")
	bus.add_signal_receiver(modem_changed,
		dbus_interface = "org.ofono.Modem",
		signal_name = "PropertyChanged")
	bus.add_signal_receiver(voice_call_added,
		dbus_interface = "org.ofono.VoiceCallManager",
		signal_name = "CallAdded")
	bus.add_signal_receiver(voice_call_removed,
		dbus_interface = "org.ofono.VoiceCallManager",
		signal_name = "CallRemoved")
	bus.add_signal_receiver(voice_call_changed,
		dbus_interface = "org.ofono.VoiceCall",
		signal_name = "PropertyChanged")

def get_hfp_properties(path):
	global inband_ringing
	hfp = dbus.Interface(bus.get_object("org.ofono", path),
                            HANDS_FREE_INTERFACE)
	properties = hfp.GetProperties()
	battery_level = int(properties['BatteryChargeLevel'])
	inband_ringing = properties['InbandRinging']
	print("In-band ringing:", inband_ringing)
	print("Current battery level:", battery_level)
	if battery_level == 0: play_battery_sound()

if __name__ == '__main__':
	subprocess.call(["aplay", FILE_DIR_PATH + "bluetooth_mode.wav"])

	dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

	bus = dbus.SystemBus()

	agent = Agent(bus, AGENT_PATH)

	obj = bus.get_object("org.bluez", "/org/bluez")
	manager = dbus.Interface(obj, "org.bluez.AgentManager1")
	manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")

	print("A2DP/HFP Agent Registered")

	manager.RequestDefaultAgent(AGENT_PATH)

	mainloop = GObject.MainLoop()

	add_signal_receivers()
	connect_to_dev()


	mainloop.run()
