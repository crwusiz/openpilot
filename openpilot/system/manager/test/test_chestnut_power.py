import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import openpilot.system.manager.modeld_recovery as recovery


class USBError(Exception):
  pass


class TestChestnutPower(unittest.TestCase):
  def setUp(self):
    self.device = {'vendorId': 0xADD1, 'productId': 1, 'product': 'custom test-CLEAN', 'busnum': 1, 'devnum': 4}
    self.devices = Mock(return_value=[self.device])
    self.handle = Mock()
    self.handle.controlRead.return_value = struct.pack('<Hh?', 12000, -40, False)
    self.context = MagicMock()
    self.context.__enter__.return_value = self.context
    self.context.openByVendorIDAndProductID.return_value = self.handle
    self.usb_context = Mock(return_value=self.context)

    def is_chestnut(vendor_id, product_id, include_bootloader=False):
      ids = {(0xADD1, 1), (0x3801, 1)}
      if include_bootloader:
        ids.add((0x174C, 0x2464))
      return (vendor_id, product_id) in ids

    modules = {
      'usb1': SimpleNamespace(USBError=USBError, USBContext=self.usb_context),
      'openpilot.common.hardware.usb': SimpleNamespace(CHESTNUT_USB_PRODUCT='custom test-CLEAN',
                                                     get_usb_state=self.devices, is_chestnut_usb_id=is_chestnut),
      'openpilot.system.hardware.chestnut.status': SimpleNamespace(CHESTNUT_POWERED_VOLTAGE=5000),
    }
    self.module_patch = patch.dict(sys.modules, modules)
    self.module_patch.start()
    self.addCleanup(self.module_patch.stop)

  def test_reads_only_live_supply_and_returns_usb_identity(self):
    self.assertEqual(recovery.read_chestnut_power(), (1, 4))
    self.context.openByVendorIDAndProductID.assert_called_once_with(0xADD1, 1, skip_on_error=True)
    self.handle.controlRead.assert_called_once_with(0xC0, 0xC0, 0, 0, 5, timeout=100)
    self.handle.controlWrite.assert_not_called()
    self.handle.close.assert_called_once_with()
    self.context.__exit__.assert_called_once()

  def test_requires_powered_supply_without_fault(self):
    for voltage, fault, expected in ((4999, False, None), (5000, False, (1, 4)), (12000, True, None)):
      with self.subTest(voltage=voltage, fault=fault):
        self.handle.controlRead.return_value = struct.pack('<Hh?', voltage, 0, fault)
        self.assertEqual(recovery.read_chestnut_power(), expected)

  def test_missing_device_never_opens_usb(self):
    self.devices.return_value = []
    self.assertIsNone(recovery.read_chestnut_power())
    self.usb_context.assert_not_called()

  def test_ignores_other_usb_devices(self):
    other = {**self.device, 'vendorId': 0x1234}
    self.devices.return_value = [other, self.device]
    self.assertEqual(recovery.read_chestnut_power(), (1, 4))

  def test_rejects_unexpected_firmware(self):
    self.devices.return_value = [{**self.device, 'product': 'custom older-CLEAN'}]
    self.assertIsNone(recovery.read_chestnut_power())
    self.usb_context.assert_not_called()

  def test_rejects_multiple_chestnuts_including_different_firmware(self):
    for product in ('custom test-CLEAN', 'custom older-CLEAN'):
      with self.subTest(product=product):
        self.devices.return_value = [self.device, {**self.device, 'product': product, 'devnum': 5}]
        self.assertIsNone(recovery.read_chestnut_power())
        self.usb_context.assert_not_called()

  def test_failed_open_closes_context(self):
    self.context.openByVendorIDAndProductID.return_value = None
    self.assertIsNone(recovery.read_chestnut_power())
    self.handle.close.assert_not_called()
    self.context.__exit__.assert_called_once()

  def test_usb_open_error_closes_context(self):
    self.context.openByVendorIDAndProductID.side_effect = USBError('disconnected')
    self.assertIsNone(recovery.read_chestnut_power())
    self.context.__exit__.assert_called_once()

  def test_failed_supply_read_always_closes_handle(self):
    for error in (USBError('disconnected'), OSError('read failed')):
      with self.subTest(error=error):
        self.handle.reset_mock()
        self.handle.controlRead.side_effect = error
        self.assertIsNone(recovery.read_chestnut_power())
        self.handle.close.assert_called_once_with()

  def test_malformed_supply_response_always_closes_handle(self):
    for response in (b'', b'\x00' * 4, b'\x00' * 6):
      with self.subTest(length=len(response)):
        self.handle.reset_mock()
        self.handle.controlRead.return_value = response
        self.assertIsNone(recovery.read_chestnut_power())
        self.handle.close.assert_called_once_with()


if __name__ == '__main__':
  unittest.main()
