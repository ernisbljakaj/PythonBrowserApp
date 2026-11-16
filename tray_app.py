import threading
import pystray
from PIL import Image, ImageDraw

class TrayApp:
    def __init__(self, on_settings, on_exit):
        self.active=True;self.on_settings=on_settings;self.on_exit=on_exit
        image=Image.new("RGB",(16,16),(30,100,210));ImageDraw.Draw(image).ellipse((4,4,12,12),fill="white")
        self.icon=pystray.Icon("AIClipboardCloud",image,"AI Clipboard",pystray.Menu(
            pystray.MenuItem(lambda _: "Status: Aktiv" if self.active else "Status: Pausiert",None,enabled=False),
            pystray.MenuItem("Aktivieren",self.enable),pystray.MenuItem("Pausieren",self.pause),
            pystray.MenuItem("Einstellungen",self.settings),pystray.MenuItem("Beenden",self.quit)))
    def enable(self,icon,item): self.active=True;icon.update_menu()
    def pause(self,icon,item): self.active=False;icon.update_menu()
    def settings(self,icon,item): threading.Thread(target=self.on_settings,daemon=True).start()
    def quit(self,icon,item): self.on_exit();icon.stop()
    def start(self): threading.Thread(target=self.icon.run,daemon=True).start()
    def stop(self): self.icon.stop()
