import threading
import os
import sys

class TrayApp:
    def __init__(self, on_settings, on_exit):
        import pystray
        from PIL import Image
        self.active=True;self.on_settings=on_settings;self.on_exit=on_exit
        icon_path=os.path.join(getattr(sys,"_MEIPASS",os.path.dirname(__file__)),"windows.ico")
        image=Image.open(icon_path)
        self.icon=pystray.Icon("Windowssystemhelper32",image,"Windowssystemhelper32",pystray.Menu(
            pystray.MenuItem(lambda _: "Status: Aktiv" if self.active else "Status: Pausiert",None,enabled=False),
            pystray.MenuItem("Aktivieren",self.enable),pystray.MenuItem("Pausieren",self.pause),
            pystray.MenuItem("Einstellungen",self.settings),pystray.MenuItem("Beenden",self.quit)))
    def enable(self,icon,item): self.active=True;icon.update_menu()
    def pause(self,icon,item): self.active=False;icon.update_menu()
    def settings(self,icon,item): threading.Thread(target=self.on_settings,daemon=True).start()
    def quit(self,icon,item): self.on_exit();icon.stop()
    def start(self): threading.Thread(target=self.icon.run,daemon=True).start()
    def stop(self): self.icon.stop()
