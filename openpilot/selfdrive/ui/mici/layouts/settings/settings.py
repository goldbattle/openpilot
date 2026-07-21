from openpilot.common.params import Params
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.selfdrive.ui.mici.widgets.button import BigButton
from openpilot.selfdrive.ui.mici.layouts.settings.toggles import TogglesLayoutMici
from openpilot.selfdrive.ui.mici.layouts.settings.network.network_layout import NetworkLayoutMici
from openpilot.selfdrive.ui.mici.layouts.settings.device import DeviceLayoutMici, PairBigButton
from openpilot.selfdrive.ui.mici.layouts.settings.developer import DeveloperLayoutMici
from openpilot.selfdrive.ui.mici.layouts.settings.software import SoftwareLayoutMici
from openpilot.selfdrive.ui.mici.layouts.recorder import RecorderLayout
from openpilot.selfdrive.ui.mici.layouts.upload import SmbSettingsPage, draw_up_arrow
from openpilot.system.ui.lib.application import gui_app, FontWeight


class SettingsBigButton(BigButton):
  def _get_label_font_size(self):
    return 64


class UploadBigButton(SettingsBigButton):
  """recorder fork: no upward-arrow asset in icons_mici/, so draw it in the same
  top-right slot BigButton puts an icon texture."""
  ICON_SIZE = 64

  def _draw_content(self, btn_y: float):
    super()._draw_content(btn_y)  # icon is None, so this just lays out the label
    draw_up_arrow(self._rect.x + self._rect.width - 30 - self.ICON_SIZE / 2,
                  btn_y + 30 + self.ICON_SIZE / 2, self.ICON_SIZE * 0.8)


class SettingsLayout(NavScroller):
  def __init__(self):
    super().__init__()
    self._params = Params()

    toggles_panel = TogglesLayoutMici()
    toggles_btn = SettingsBigButton("toggles", "", gui_app.texture("icons_mici/settings.png", 64, 64))
    toggles_btn.set_click_callback(lambda: gui_app.push_widget(toggles_panel))

    network_panel = NetworkLayoutMici()
    network_btn = SettingsBigButton("network", "", gui_app.texture("icons_mici/settings/network/wifi_strength_full.png", 76, 56))
    network_btn.set_click_callback(lambda: gui_app.push_widget(network_panel))

    device_panel = DeviceLayoutMici()
    device_btn = SettingsBigButton("device", "", gui_app.texture("icons_mici/settings/device_icon.png", 72, 58))
    device_btn.set_click_callback(lambda: gui_app.push_widget(device_panel))

    software_panel = SoftwareLayoutMici()
    software_btn = SettingsBigButton("software", "", gui_app.texture("icons_mici/settings/software.png", 64, 75))
    software_btn.set_click_callback(lambda: gui_app.push_widget(software_panel))

    developer_panel = DeveloperLayoutMici()
    developer_btn = SettingsBigButton("developer", "", gui_app.texture("icons_mici/settings/developer_icon.png", 64, 60))
    developer_btn.set_click_callback(lambda: gui_app.push_widget(developer_panel))

    # recorder fork: SMB server config + "upload all".
    upload_panel = SmbSettingsPage()
    upload_btn = UploadBigButton("upload", "", None)
    upload_btn.set_click_callback(lambda: gui_app.push_widget(upload_panel))

    # recorder fork: manual recorder, for capturing with no key in the ignition. With the
    # key on the device goes onroad and records the drive by itself, so this is only for
    # the car-off case. The route list is not here -- it's under the upload panel.
    recorder_panel = RecorderLayout()
    recorder_btn = SettingsBigButton("record", "", gui_app.texture("icons_mici/settings/developer_icon.png", 64, 60))
    recorder_btn.set_click_callback(lambda: gui_app.push_widget(recorder_panel))

    # recorder fork: firehose removed — it advertises uploading data to comma. This fork keeps
    # all data on device (uploader + athenad are disabled in process_config).

    self._scroller.add_widgets([
      toggles_btn,
      network_btn,
      recorder_btn,
      upload_btn,
      device_btn,
      software_btn,
      PairBigButton(),
      #BigDialogButton("manual", "", "icons_mici/settings/manual_icon.png", "Check out the mici user\nmanual at comma.ai/setup"),
      developer_btn,
    ])

    self._font_medium = gui_app.font(FontWeight.MEDIUM)
