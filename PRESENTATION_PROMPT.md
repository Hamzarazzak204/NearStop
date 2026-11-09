# Prompt: build our 5-minute NearStop pitch (5 speakers)

You're helping a student team at **natHacks 2026** (Edmonton, in person, **Construction & Manufacturing** theme) prepare a **5-minute live presentation** of their project, **NearStop**. **Five team members** will each speak. Several are beginners at presenting. Read this whole brief before writing anything.

## What I want from you

1. **A speaker-by-speaker script** for 5 speakers, about **55 seconds each** (5:00 total including hand-offs). Write it the way people talk: short sentences, no jargon, with a one-line hand-off to the next speaker at the end of each part.
2. **A slide outline**: at most **6 slides**, one idea per slide, with what's on it (a headline, one visual, a few words). No walls of text.
3. **A live-demo plan** (about 60-90 s, inside the 5 minutes): who does what, with a **backup** if the hardware or Wi-Fi fails (pre-recorded video, or NearStop's built-in simulator).
4. **Q&A prep**: the 8-10 questions judges are most likely to ask, with short honest answers, and which speaker takes each.
5. **A timing sheet**: start/end time per speaker, so we can rehearse with a stopwatch.

Speaker names and who-does-what: use **Speaker 1-5** placeholders, and ask me for names/roles before finalising. Suggested split (adjust if a better flow appears):

| Speaker | Part |
|---|---|
| 1 | The problem: a worker near heavy machinery, and what happens when they fall |
| 2 | Our solution in one picture: machines that see you + a strap that feels you fall |
| 3 | Live demo (with Speaker 4 operating the dashboard) |
| 4 | The supervisor's dashboard: alerts, check-in, lockout, monitoring |
| 5 | What we proved, what's next, the ask/closing line |

## The project in one paragraph

**NearStop** is an extra safety layer for worksites. Each machine has an **ultrasonic distance sensor** watching the area in front of it. The worker wears a **strap** on the upper arm with a **motion sensor** that detects falls. A laptop (or Raspberry Pi) runs the **NearStop hub and dashboard**. When a worker gets too close, the machine **slows**, then **stops**. If the worker **falls near a machine, that machine is locked out** and can't restart until a supervisor approves it, and only once the area is clear. **Every fall raises an alert** with a 10-second check-in. If nobody answers, it becomes an **SOS**. The worker can press an **"I'm OK" button** on the strap. The strap also carries **temperature/air-pressure and combustible-gas sensors**, shown on a **Monitoring** tab for the supervisor.

**Pitch angle:** proximity systems know how close a worker is to a machine. Man-down wearables know a worker fell. **We couldn't find a product that combines the two around a specific machine.** Say it exactly like that, never "nobody does this".

## The demo setup (what's on the table)

| ID | Device | What it does |
|---|---|---|
| GW | **Robot Arm** | Ultrasonic sensor. Slows when someone is within 1.5 m (warning), stops within 0.5 m (danger) |
| MA | **Conveyor** | Same as the Robot Arm |
| MB | **Hoist** (real motor) | Keeps running when someone just stands near it. **Holds instantly** when the strap feels a hard hit nearby, and **locks out** if the fall is confirmed |
| W1 | **"Sam", the worker strap** | Fall detection, zone light (green/yellow/red), close-to-machine LED, "I'm OK" button, temperature/air-pressure + gas sensors, fall buzzer |

All boards are ESP32 microcontrollers. They report to the hub over **Wi-Fi, or over USB** when Wi-Fi is weak (our boards came without antennas, so we built a USB fallback that switches over in about a second).

## How it works (use for Speaker 2 and 4)

- **Zones** are a **1:6 scale** of real haul-truck proximity systems (one industry design uses about a 3-4.5 m danger zone and a 6-9 m warning zone). So, on our table: **danger ≤ 50 cm**, **warning ≤ 150 cm**.
- **Machine reactions:** warning zone → **Slowed**. Danger zone → **Stopped**, and it stays stopped until a supervisor presses **Approve restart**.
- **Fall detection** (on the strap, ~100 readings per second): free-fall dip (below 0.85 g) → impact of 2.5 g or more (3.5 g with no free fall) → the worker lying still for 1 s. That last step is what rejects hammering, jumping or sitting down hard. A fall is confirmed **about 1.3 s after impact**.
- **Why it matters for the Hoist:** 1.3 s is too slow for a hoist. So the strap sends an instant **"impact"** message the moment it feels a hard hit close to a machine, and the Hoist **holds within ~0.03 s** (measured). If the fall is confirmed, the hold becomes a **lockout**; if the worker keeps moving, the Hoist resumes after 3 s.
- **Every fall is an alert.** Near a machine → that machine is **locked out**. Away from machines → a red **Fall** banner + alarm + 10 s check-in, and **no answer = SOS**.
- **"I'm OK" button:** answers the check-in, so no SOS. It **never** unlocks a machine: a person who fell can press a button and still be hurt, so the supervisor decides. (Team decision; good line for Q&A.)
- **Restart is refused** while the check-in is open or anyone is still in the zone.
- **Evacuate** button: locks every machine at once.
- **Monitoring tab:** live temperature, air pressure, and combustible gas from the strap, with 10-minute charts. Gas is shown as **"% of clean air"**: the sensor learns clean air after a 2-minute warm-up, then turns yellow at 60 % and red at 35 %. This mirrors the **2-stage alarm used by real site gas monitors** (low alarm at 10 % of the explosive limit, high alarm at 20 %; Alberta OHS Code s.162 bars work above 20 %). **Our sensor is uncalibrated**, so we never claim ppm or % of the explosive limit.

## What we actually proved on hardware (be honest; judges respect it)

**Tested on the real boards:**
- Robot Arm: a worker in the red zone → fall alert confirmed by the hub in **under 0.1 s**, Robot Arm **locked out**, restart blocked until clear.
- Hoist: **~0.03 s** from the hit to the Hoist holding.
- Strap: a real **drop at 5.5 g was detected and alerted** (a 4.6 g hit where the strap kept moving was correctly **rejected** as "not a fall"). Then **"I'm OK" pressed 2 s later** was received and confirmed.
- A fall **away from all machines** raises an alert and check-in, with nothing locked out.
- Wi-Fi ↔ USB switch-over in about 1 s.
- **67 automated tests** of the safety logic pass, plus every board's firmware compiles.

**Not proven yet. Don't claim these:**
- Fall thresholds come from research (trunk sensors: smallest fall impacts about 3.5 g; normal activity 1-2.5 g) but **haven't been tuned on real falls onto a mat**.
- **Temperature/pressure sensor (BMP180):** working on the real strap (e.g. 23.4 °C, 924 hPa); monitoring only, no alarms from pressure.
- **Gas sensor:** wiring was being fixed. Only claim "it reacts to gas" if we've run the lighter/alcohol test before the pitch.
- Built and tested with **one worker**. An ultrasonic sensor sees the nearest **object**, not *who* it is, and only in a narrow ~20° cone. Keep the demo area clear.

## Suggested live demo (≈ 75 s)

1. Sam (wearing the strap) walks toward the **Conveyor**. The strap light goes **green → yellow** and the Conveyor shows **Slowed** on screen.
2. Sam steps closer: **red**, and the Conveyor **Stops**.
3. Sam steps to the **Hoist** and drops onto a mat (or the strap is dropped onto a cushion). The Hoist **holds instantly**, then is **locked out**. A red **Fall** banner and alarm appear, with the check-in countdown.
4. Sam presses **"I'm OK"**. The SOS is avoided, but the Hoist **stays locked**: the supervisor sees the area is clear and presses **Approve restart**.
5. (If time) flip to the **Monitoring** tab for temperature/pressure/gas.

**Backup if hardware fails:** play a pre-recorded video of the same sequence, or run NearStop's **simulator** (Setup → Tools → "NearStop demo" scenario), which acts out approach → fall → SOS → restart on the real dashboard. Say plainly that it's the simulator.

## Wording rules (please follow)

- Lead with **the worker**, not part numbers ("motion sensor", not "MPU-6050"; "distance sensor", not "HC-SR04").
- Give **measured numbers**, or say "not measured yet". Never "detects every fall".
- It's an **extra safety layer**, never a replacement for guards, certified e-stops or lockout/tagout.
- **No medical claims.**
- Gas: "detects a rise in combustible gas compared to clean air", never ppm or "% LEL".
- "We couldn't find a product that combines…", never "nobody does this".

## Likely judge questions (start from these)

- *What about a fall away from any machine?* → It still alerts, with a check-in and SOS; it just doesn't lock a machine.
- *Why not let "I'm OK" unlock the machine?* → Someone who fell can press a button and still be hurt; a supervisor confirms the area is safe.
- *How do you avoid false alarms from hammering or jumping?* → The strap requires the worker to be still for 1 s after the impact; a 4.6 g hit with movement was rejected in testing.
- *How fast does it react?* → Hoist holds in ~0.03 s after a hit; fall confirmed ~1.3 s after impact; hub confirms alerts in under 0.1 s.
- *Can the sensor tell who is in front of the machine?* → No, ultrasonic sees the nearest object. With more workers you'd add tags/UWB; we tested with one.
- *Is the gas reading accurate?* → It's relative to clean air and uncalibrated; it flags a rise early. A real deployment would use a certified, calibrated detector.
- *What if Wi-Fi drops?* → The boards fall back to USB automatically; a real site would use antennas and a dedicated network.
- *What's next?* → Real mat-fall tuning, multiple workers, antennas/wireless, vibration alert on the strap, physical machine interlocks.

## Event facts

- natHacks 2026, Edmonton. Build window ends **Mon Nov 9, 2026, 11:00 AM MST**.
- Presentation: **5 minutes** (keep to time; plan to finish at 4:45), then questions.
- The repo is public; nothing secret goes on slides.
