#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sphero BOLT — Autorun (oneindig) op afstand + draaien, met calibratie-offset
- Geen joystick
- Auto-calibratie (best-effort): heading-offset + snelheidsfactor (als locator beschikbaar)
- Rijdt segmenten: (afstand_in_meter -> draai_in_graden), negatief = rechtsaf
- Blijft rondjes rijden tot Ctrl+C

Benodigd:
  pip install spherov2 bleak dbus-next
Gebruik:
  python3 sphero_autorun_distance.py SB-27A5
"""

import sys
import time
import math
from spherov2 import scanner
from spherov2.types import Color
from spherov2.sphero_edu import SpheroEduAPI
from spherov2.commands.power import Power

# ------------------ Instellingen ------------------
COUNTDOWN_S = 3
PAUSE_SEG   = 0.07

# Snelheidsmodel (wordt bijgesteld als kalibratie lukt)
M_PER_S_AT_170 = 0.60

STRAIGHT_SPEED = 180       # rechte stukken
TURN_SPEED     = 140       # bochten
HEADING_OFFSET = 0.0       # wordt gezet bij auto-calibratie (graden)

# Segmenten volgens je foto: (afstand in m, draai NA het stuk in °)
# 0° = naar rechts (eerste pijl). Negatief = rechtsaf, positief = linksaf.
SEGMENTS_DISTANCE_TURNS = [
    (2.30, -90),  # top van X naar rechts, dan omlaag
    (2.40, -45),  # langs rechterzijde naar beneden -> begin lange bocht
    (0.70, -35),
    (0.90, -25),
    (0.90, -15),
    (0.80, +60),  # linksom omhoog draaien
    (1.80, -90),  # omhoog, hoek rechtsaf
    (2.30,   0),  # over de top terug naar de finish
]

# ------------------ Helpers ------------------
def norm_deg(a):
    return (a + 360.0) % 360.0

def apply_offset(hdg):
    """Logische heading -> fysiek commando, corrigeert met calibratie-offset."""
    return norm_deg(hdg + HEADING_OFFSET)

def safe_matrix_char(api, ch, color):
    """ASCII matrix; fallback naar front-LED als set_matrix faalt."""
    try:
        if not isinstance(ch, str) or len(ch) != 1 or ord(ch) > 127:
            ch = "V"
        api.set_matrix_character(ch, color)
    except Exception:
        try:
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
    except Exception:
        return
    if v > 4.1:
        api.set_front_led(Color(0, 255, 0))
    elif v > 3.9:
        api.set_front_led(Color(255, 255, 0))
    elif v > 3.7:
        api.set_front_led(Color(255, 128, 0))
    else:
        api.set_front_led(Color(255, 0, 0))
    print(f"[INFO] Battery: {v:.2f} V")

def supports_locator(api):
    return hasattr(api, "get_location") and callable(getattr(api, "get_location"))

def try_reset_locator(api):
    """Reset de relatieve positie naar (0,0) als de API dat ondersteunt."""
    try:
        api.reset_locator()
        return True
    except AttributeError:
        pass
    for fname in ("set_location", "set_locator", "set_position"):
        try:
            getattr(api, fname)(0, 0)
            return True
        except Exception:
            continue
    return False

# ------------------ Auto-calibratie ------------------
def auto_calibrate(api, pulse_speed=120, pulse_time=0.70):
    """
    Best-effort calibratie:
      - Als locator beschikbaar: korte puls vooruit op logische 0°,
        meet dx,dy -> bepaal HEADING_OFFSET en stel M_PER_S_AT_170 bij.
      - Anders: sla over en gebruik default HEADING_OFFSET.
    """
    global HEADING_OFFSET, M_PER_S_AT_170

    print("[CAL] Auto-calibratie...")
    api.set_speed(0)
    safe_matrix_char(api, "C", Color(255, 255, 0))
    time.sleep(0.2)

    if not supports_locator(api):
        print("[CAL] Locator niet beschikbaar -> overslaan (gebruik vaste offset).")
        safe_matrix_char(api, "V", Color(0, 200, 255))
        time.sleep(0.2)
        return

    if not try_reset_locator(api):
        print("[CAL] Locator kon niet gereset worden -> overslaan (vaste offset).")
        safe_matrix_char(api, "V", Color(0, 200, 255))
        time.sleep(0.2)
        return

    # Puls vooruit op 'logische' 0° (naar rechts)
    api.set_heading(int(apply_offset(0)))
    api.set_speed(pulse_speed)
    time.sleep(pulse_time)
    api.set_speed(0)
    time.sleep(0.25)

    try:
        loc = api.get_location()
        dx = (loc.get('x', 0.0)) / 1000.0
        dy = (loc.get('y', 0.0)) / 1000.0
    except Exception:
        print("[CAL] get_location() faalde -> overslaan (vaste offset).")
        safe_matrix_char(api, "V", Color(0, 200, 255))
        time.sleep(0.2)
        return

    dist = math.hypot(dx, dy)
    if dist < 0.02:
        print("[CAL] Te weinig verplaatsing -> overslaan (vaste offset).")
        safe_matrix_char(api, "V", Color(0, 200, 255))
        time.sleep(0.2)
        return

    # Werkelijke bewegingshoek bij commando 0°
    angle_moved = norm_deg(math.degrees(math.atan2(-dy, dx)))
    # Corrigeer zodat 'logische 0°' fysiek ook naar rechts wordt
    HEADING_OFFSET = norm_deg(-angle_moved)

    # Snelheidsmodel bijstellen (m/s bij 170)
    v_meas = dist / pulse_time
    M_PER_S_AT_170 = max(0.45, min(0.85, v_meas * 170.0 / pulse_speed))

    print(f"[CAL] dx={dx:.3f} m, dy={dy:.3f} m, angle={angle_moved:.1f}°, "
          f"OFFSET={HEADING_OFFSET:.1f}°, M170≈{M_PER_S_AT_170:.2f} m/s")
    safe_matrix_char(api, "V", Color(0, 200, 255))
    time.sleep(0.2)

# ------------------ Afstand + draaien ------------------
def drive_forward_distance(api, heading_deg, distance_m, speed, timeout_factor=2.0):
    """
    Rijd rechtdoor tot 'distance_m' bereikt is.
    - Met locator: meet d = hypot(x-x0, y-y0)
    - Zonder locator: fallback op tijd (m/s-model)
    Heading-commando wordt gecorrigeerd met HEADING_OFFSET.
    """
    use_locator = supports_locator(api) and try_reset_locator(api)

    api.set_heading(int(apply_offset(heading_deg)))
    api.set_speed(int(speed))

    if use_locator:
        try:
            loc0 = api.get_location()
            x0 = loc0.get('x', 0.0) / 1000.0
            y0 = loc0.get('y', 0.0) / 1000.0
        except Exception:
            use_locator = False

    if use_locator:
        t0 = time.time()
        v_ms = M_PER_S_AT_170 * (speed / 170.0)
        est = max(0.1, distance_m / max(v_ms, 1e-6))
        while True:
            try:
                loc = api.get_location()
                x = loc.get('x', 0.0) / 1000.0
                y = loc.get('y', 0.0) / 1000.0
                d = math.hypot(x - x0, y - y0)
                if d >= distance_m:
                    break
            except Exception:
                break
            if time.time() - t0 > est * timeout_factor:
                break
            time.sleep(0.02)
    else:
        v_ms = M_PER_S_AT_170 * (speed / 170.0)
        dur = distance_m / max(v_ms, 1e-6)
        time.sleep(dur)

    api.set_speed(0)
    time.sleep(PAUSE_SEG)

def turn_by(api, current_heading_deg, delta_deg):
    """
    Pas de LOGISCHE heading aan met delta_deg; stuur met offset.
    (Sphero draait niet in place – we wijzigen de aanstuur-heading.)
    """
    new_hdg = norm_deg(current_heading_deg + delta_deg)
    api.set_heading(int(apply_offset(new_hdg)))
    time.sleep(0.05)
    return new_hdg

def run_lap_by_distance(api, start_heading_deg=0):
    """Rijdt één ronde op basis van afstand->draai segmenten."""
    safe_matrix_char(api, "D", Color(0, 255, 0))  # D = Distance mode
    t0 = time.time()
    current_hdg = norm_deg(start_heading_deg)  # 0° = naar rechts (eerste pijl)

    for idx, (dist_m, turn_deg) in enumerate(SEGMENTS_DISTANCE_TURNS, start=1):
        spd = TURN_SPEED if abs(turn_deg) > 20 else STRAIGHT_SPEED
        print(f"  segment {idx}: {dist_m:.2f} m @ {spd}, daarna draai {turn_deg:+.0f}°")
        drive_forward_distance(api, current_hdg, dist_m, spd)
        current_hdg = turn_by(api, current_hdg, turn_deg)

    lap_time = time.time() - t0
    safe_matrix_char(api, "V", Color(0, 255, 0))
    print(f"[FINISH] Rondetijd (distance-mode): {lap_time:.2f} s")
    time.sleep(0.2)

# ------------------ Verbinden + main ------------------
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
        if (t.name or "").strip() == target_name:
            print(f"[INFO] Exacte match in scan: {t.name}")
            return t
    for t in toys:
        if (t.name or "").startswith("SB-"):
            print(f"[INFO] Neem dichtstbijzijnde: {t.name}")
            return t
    print("[ERR] Geen SB- devices met SB- gevonden.")
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

                # 1) Auto-calibratie (heading-offset + m/s, indien mogelijk)
                auto_calibrate(api)

                # 2) Countdown en oneindig rondjes op distance-mode
                print(f"[INFO] Start over {COUNTDOWN_S} s.")
                show_countdown(api, COUNTDOWN_S)

                lap = 0
                while True:
                    lap += 1
                    print(f"\n=== LAP {lap} ===")
                    run_lap_by_distance(api, start_heading_deg=0)  # 0° = naar rechts
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
        print("Gebruik: python3 sphero_autorun_distance.py <SB-naam>")
        print("Voorbeeld: python3 sphero_autorun_distance.py SB-27A5")
        sys.exit(1)

    toy_name = sys.argv[1]
    try:
        main(toy_name)
    except KeyboardInterrupt:
        print("\n[STOP] Handmatig gestopt met Ctrl+C.")
