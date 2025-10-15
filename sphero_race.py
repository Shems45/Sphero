#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sphero BOLT — Autorun (oneindig) met automatische kalibratie (best-effort), wijzerszin
- Geen joystick nodig
- Kalibratie gebruikt locator als beschikbaar; anders slaat die netjes over
- Start automatisch na 3 s (countdown)
- Blijft rondjes rijden tot Ctrl+C
"""

import sys
import time
import math
from spherov2 import scanner
from spherov2.types import Color
from spherov2.sphero_edu import SpheroEduAPI
from spherov2.commands.power import Power

# ---------- Instellingen ----------
COUNTDOWN_S = 3
PAUSE_SEG   = 0.07

# Snelheidsmodel (wordt bijgesteld als kalibratie lukt)
M_PER_S_AT_170 = 0.60

STRAIGHT_SPEED = 180       # rechte stukken
TURN_SPEED     = 140       # bochten
HEADING_OFFSET = 0.0       # vaste offset; wordt gezet als kalibratie lukt

# Waypoints in meters (paneel = 0.50 m), (0,0) = midden start/finish, +x rechts, +y omlaag
WAYPOINTS = [
    (0.00, 0.00),  # start/finish
    (2.20, 0.00),  # bovenlangs naar rechts
    (2.35, 2.35),  # rechts naar beneden
    (1.95, 2.00),  # boog naar links-in
    (1.55, 1.65),
    (1.20, 1.45),
    (0.80, 1.30),  # door het midden linkswaarts
    (0.60, 0.70),  # omhoog richting linksboven
    (0.35, 0.25),  # vlak voor bovenrand
    (0.00, 0.00),  # finish
]


SEGMENTS_DISTANCE_TURNS = [
    (2.20, -90),  # rij 2.20 m → rechtsaf
    (2.35, -35),  # rij 2.35 m → lichte bocht rechts (voorbeeld)
    (0.60, -25),
    (0.55, -20),
    (0.45, -15),
    (0.45, -20),
    (0.70, -60),
    (0.50, -35),
    (0.35,   0),  # laatste stuk → geen extra draai nodig
]

# ---------- Helpers ----------
def heading_deg(p0, p1):
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    return math.degrees(math.atan2(-dy, dx)) % 360

def dist_m(p0, p1):
    return math.hypot(p1[0] - p0[0], p1[1] - p0[1])

def apply_offset(hdg):
    return (hdg + HEADING_OFFSET) % 360

def safe_matrix_char(api, ch, color):
    """Zet een ASCII-teken op de matrix; bij fout val terug op front-LED."""
    try:
        if not isinstance(ch, str) or len(ch) != 1 or ord(ch) > 127:
            ch = "V"  # veilige fallback
        api.set_matrix_character(ch, color)
    except Exception:
        try:
            # korte “blink” met front-LED als fallback
            api.set_front_led(color)
        except Exception:
            pass

def show_countdown(api, seconds=3):
    for n in range(seconds, 0, -1):
        safe_matrix_char(api, str(n)[0], Color(0, 255, 0))
        time.sleep(1.0)
    safe_matrix_char(api, ">", Color(0, 255, 0))

def battery_led(api, toy):
    try:
        v = Power.get_battery_voltage(toy)
        if v > 4.1:
            api.set_front_led(Color(0, 255, 0))
        elif v > 3.9:
            api.set_front_led(Color(255, 255, 0))
        elif v > 3.7:
            api.set_front_led(Color(255, 128, 0))
        else:
            api.set_front_led(Color(255, 0, 0))
        print(f"[INFO] Battery: {v:.2f} V")
    except Exception:
        pass

def supports_locator(api):
    return hasattr(api, "get_location") and callable(getattr(api, "get_location"))

def try_reset_locator(api):
    try:
        api.reset_locator(); return True
    except AttributeError:
        pass
    for fname in ("set_location", "set_locator", "set_position"):
        try:
            getattr(api, fname)(0, 0)
            return True
        except Exception:
            continue
    return False

def auto_calibrate(api, pulse_speed=120, pulse_time=0.70):
    """Best-effort kalibratie via locator; overslaan als niet beschikbaar."""
    global HEADING_OFFSET, M_PER_S_AT_170

    print("[CAL] Auto-calibratie...")
    api.set_speed(0)
    safe_matrix_char(api, "C", Color(255, 255, 0))  # C = Calibrate
    time.sleep(0.2)

    if not supports_locator(api):
        print("[CAL] Locator niet beschikbaar → kalibratie overgeslagen (vaste HEADING_OFFSET).")
        safe_matrix_char(api, "V", Color(0, 200, 255))
        time.sleep(0.3)
        return

    if not try_reset_locator(api):
        print("[CAL] Locator kon niet gereset worden → kalibratie overgeslagen (vaste HEADING_OFFSET).")
        safe_matrix_char(api, "V", Color(0, 200, 255))
        time.sleep(0.3)
        return

    # Puls vooruit op heading 0°
    api.set_heading(0)
    api.set_speed(pulse_speed)
    time.sleep(pulse_time)
    api.set_speed(0)
    time.sleep(0.25)

    try:
        loc = api.get_location()
        dx = (loc.get('x', 0.0)) / 1000.0
        dy = (loc.get('y', 0.0)) / 1000.0
    except Exception:
        print("[CAL] get_location() mislukt → kalibratie overgeslagen (vaste HEADING_OFFSET).")
        safe_matrix_char(api, "V", Color(0, 200, 255))
        time.sleep(0.3)
        return

    dist = math.hypot(dx, dy)
    if dist < 0.02:
        print("[CAL] Te weinig verplaatsing gemeten → kalibratie overgeslagen (vaste HEADING_OFFSET).")
        safe_matrix_char(api, "V", Color(0, 200, 255))
        time.sleep(0.3)
        return

    angle_moved = (math.degrees(math.atan2(-dy, dx)) % 360)
    HEADING_OFFSET = (-angle_moved) % 360

    v_meas = dist / pulse_time
    M_PER_S_AT_170 = max(0.45, min(0.85, v_meas * 170.0 / pulse_speed))

    print(f"[CAL] dx={dx:.3f} m, dy={dy:.3f} m, angle={angle_moved:.1f}°, "
          f"OFFSET={HEADING_OFFSET:.1f}°, M170≈{M_PER_S_AT_170:.2f} m/s")

    safe_matrix_char(api, "V", Color(0, 200, 255))  # V = “ok”/check

def run_lap(api):
    """Rijdt één volledige ronde volgens WAYPOINTS (met offset-correctie)."""
    safe_matrix_char(api, "A", Color(0, 255, 0))  # A = Auto
    t0 = time.time()
    prev_hdg = None

    for i in range(1, len(WAYPOINTS)):
        p0, p1 = WAYPOINTS[i-1], WAYPOINTS[i]
        hdg = heading_deg(p0, p1)
        d   = dist_m(p0, p1)

        if prev_hdg is None:
            spd = STRAIGHT_SPEED
        else:
            turn = abs((hdg - prev_hdg + 540) % 360 - 180)
            spd = TURN_SPEED if turn > 20 else STRAIGHT_SPEED

        v_ms = M_PER_S_AT_170 * (spd / 170.0)
        dur  = d / max(v_ms, 1e-6)

        cmd_hdg = apply_offset(hdg)
        api.set_heading(int(cmd_hdg))
        api.set_speed(int(spd))
        time.sleep(dur)
        api.set_speed(0)
        time.sleep(PAUSE_SEG)

        prev_hdg = hdg
        print(f"  segment {i}: req={int(hdg)}° cmd={int(cmd_hdg)}° d={d:.2f} m v={spd}")

    lap_time = time.time() - t0
    safe_matrix_char(api, "V", Color(0, 255, 0))  # V ≈ “ok”
    print(f"[FINISH] Rondetijd: {lap_time:.2f} s")
    time.sleep(0.2)

# ---------- Hoofdprogramma ----------
def pick_toy_by_name_or_scan(target_name: str):
    toy = scanner.find_toy(toy_name=target_name)
    if toy:
        print(f"[INFO] Gevonden via naam: {target_name}")
        return toy
    print("[WARN] Niet direct gevonden. Scannen naar Sphero's...")
    toys = scanner.find_toys()
    if not toys:
        print("[ERR] Geen Sphero's gevonden bij scan.")
        return None
    for t in toys:
        try:
            if (t.name or "").strip() == target_name:
                print(f"[INFO] Exacte match in scan: {t.name}")
                return t
        except Exception:
            pass
    for t in toys:
        if (t.name or "").startswith("SB-"):
            print(f"[INFO] Neem dichtstbijzijnde: {t.name}")
            return t
    print("[ERR] Geen SB- devices gevonden in scan.")
    return None

def main(toy_name):
    print(f"[INFO] Zoeken naar '{toy_name}' ...")
    toy = pick_toy_by_name_or_scan(toy_name)
    if toy is None:
        sys.exit(1)

    attempts = 3
    for i in range(1, attempts + 1):
        try:
            print(f"[INFO] Verbinden (poging {i}/{attempts})...")
            with SpheroEduAPI(toy) as api:
                battery_led(api, toy)

                # 1) Kalibratie (best-effort)
                auto_calibrate(api)

                # 2) Countdown en oneindig rondjes
                print(f"[INFO] Start over {COUNTDOWN_S} s.")
                show_countdown(api, COUNTDOWN_S)

                lap = 0
                while True:
                    lap += 1
                    print(f"\n=== LAP {lap} ===")
                    run_lap(api)

                    api.set_led(Color(0, 0, 0))
                    api.set_front_led(Color(0, 255, 0))
                    if lap % 5 == 0:
                        battery_led(api, toy)
            return
        except Exception as e:
            print("[WARN] Fout tijdens run:", e)
            if i < attempts:
                print("[INFO] Opnieuw proberen...")
                time.sleep(2.5)
            else:
                print("[ERR] Kon niet stabiel draaien. Check Bluetooth/afstand en probeer opnieuw.")
                sys.exit(2)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Gebruik: python3 sphero_autorun.py <SB-naam>")
        print("Voorbeeld: python3 sphero_autorun.py SB-27A5")
        sys.exit(1)

    toy_name = sys.argv[1]
    try:
        main(toy_name)
    except KeyboardInterrupt:
        print("\n[STOP] Handmatig gestopt met Ctrl+C.")
