# Faculty-to-Course Allocation System (Prototype)

A free, open-source tool that automatically assigns faculty to course sections
using constraint optimization (Google OR-Tools CP-SAT solver) — not machine
learning. It guarantees hard rules are never broken (qualification match, max
teaching load, one faculty per section) while optimizing for soft goals
(faculty preferences, balanced workload).

---

## 1. Files in this project

```
course_allocation/
├── app.py                  # Streamlit web interface
├── solver.py                # Core CP-SAT optimization engine (can run standalone)
├── sample_data/
│   ├── faculty.csv          # Sample faculty list
│   ├── courses.csv          # Sample course list
│   └── preferences.csv      # Sample faculty preferences
└── README.md                 # This file
```

---

## 2. Running it on your own computer (free, no signup needed)

### Step 1 — Install Python
If you don't already have Python, download it free from https://python.org
(version 3.9 or newer). Confirm it's installed:
```bash
python3 --version
```

### Step 2 — Install the required libraries
```bash
pip install ortools streamlit pandas openpyxl
```
(If `pip` alone doesn't work, try `pip3` or `python3 -m pip install ...`)

### Step 3 — Run the app
From inside the `course_allocation` folder:
```bash
streamlit run app.py
```
This opens automatically in your browser at `http://localhost:8501`. The
terminal stays running the server — close it (Ctrl+C) to stop.

### Step 4 — Try it with the sample data first
The app loads bundled sample data automatically so you can click
"Run Allocation" immediately and see it work before touching your real data.

---

## 3. Switching to your real department data

Replace the sample CSVs with your own, **matching these exact column names**:

### `faculty.csv`
| Column | Meaning | Example |
|---|---|---|
| faculty_id | Unique ID | F001 |
| name | Full name | Dr. A. Sharma |
| specialization_tags | Semicolon-separated skills | `DBMS;Algorithms;DataStructures` |
| max_load | Max sections they can teach | 3 |
| current_load | (optional, informational only) | 0 |

### `courses.csv`
| Column | Meaning | Example |
|---|---|---|
| course_id | Unique ID | C001 |
| course_name | Full name | Database Management Systems |
| required_specialization | Must exactly match a tag in faculty's specialization_tags | DBMS |
| sections | How many separate sections need staffing | 2 |
| credits | Credit value (informational) | 4 |
| time_slot | When this course runs (used to prevent double-booking) | Mon-9AM |

**Important:** `required_specialization` in courses.csv must be spelled
*exactly* the same as one of the tags in a faculty member's
`specialization_tags` (case-sensitive). E.g. if a course requires `DBMS`,
at least one faculty member must have `DBMS` somewhere in their tag list.

**About `time_slot`:** if two courses share the same `time_slot` value
(meaning they happen at the same time), the solver guarantees no single
faculty member is assigned to both — they physically cannot be in two
places at once. Use any labeling scheme you like (`Mon-9AM`, `Slot-A`,
`Period-3`, etc.) as long as courses happening simultaneously share the
exact same value. If you omit this column entirely, the solver still works
but skips the no-double-booking check — the app will warn you about this.

**Note on multi-section courses:** if a course has `sections = 2`, both
sections currently share the same `time_slot` (i.e. two parallel sections
of the same course, taught by two different faculty, at the same time).
If your department actually runs sections at different times, list each
section as its own row in courses.csv (e.g. `C001-A` and `C001-B`) with
`sections = 1` each and their own time slots.

### `preferences.csv` (optional but recommended)
| Column | Meaning | Example |
|---|---|---|
| faculty_id | Must match an ID in faculty.csv | F001 |
| course_id | Must match an ID in courses.csv | C001 |
| preference_rank | 1 = most preferred, 2 = next, etc. | 1 |

You don't need to list every faculty-course pair — only the ones a faculty
member explicitly wants. Unlisted pairs are treated as neutral.

Upload these three files in the sidebar of the running app, uncheck
"Use bundled sample data," and click **Run Allocation**.

---

## 4. Understanding the two sliders

- **Preference weight** — how strongly the solver favors giving faculty
  their preferred courses. Increase this if faculty preferences keep
  getting ignored in favor of "neat" load balancing.
- **Workload balance weight** — how strongly the solver avoids overloading
  a few faculty while others sit idle. Increase this if you see some
  faculty maxed out while others are under-used.

These two often trade off against each other — there's no universally
"correct" setting; tune it based on what your department actually values.

---

## 5. What happens if allocation is impossible

If your data genuinely can't be satisfied (e.g., total teaching capacity is
less than total sections needing staff, or a course needs a specialization
nobody has), the app will tell you clearly:
- **INFEASIBLE** — capacity problem across the whole department; you'll need
  to either add faculty, reduce sections, or raise someone's max_load.
- **Unassignable section warning** — a specific course has no qualified
  faculty at all; you'll need to either hire/retrain someone or check for a
  typo in the specialization tag spelling.

---

## 6. Sharing for a quick demo (same WiFi/LAN)

If you just need your HOD or a colleague to try it on the same network as
your laptop, right now, without setting up cloud hosting:

1. Run the app telling it to listen on the network, not just your machine:
   ```bash
   streamlit run app.py --server.address 0.0.0.0
   ```
2. The terminal will print a **Network URL** like `http://192.168.1.42:8501`
   — give that exact link to the other person, while they're connected to
   the same WiFi/LAN.
3. If it doesn't load for them, your firewall is likely blocking incoming
   connections on port 8501 — temporarily allow Python/port 8501 through
   your OS firewall settings.
4. Your laptop and the terminal must both stay on and running the whole
   time they're using it — this is a temporary, fragile setup, fine for a
   one-off demo but not for ongoing departmental use.

## 7. Deploying it for real department use (still free, permanent link)

Once you've validated it against last semester's real data and want a
permanent link that works even when your laptop is off:

1. Create a free GitHub account at https://github.com (if you don't have one).
2. Create a new repository and upload `app.py`, `solver.py`, the
   `sample_data` folder, and a `requirements.txt` file containing:
   ```
   streamlit
   pandas
   ortools
   openpyxl
   ```
3. Go to https://streamlit.io/cloud, sign in with GitHub (free tier is
   generous for small internal tools like this — no credit card required).
4. Click "New app," point it at your repository and `app.py`.
5. You'll get a public URL (e.g. `yourapp.streamlit.app`) that your
   department can bookmark and use directly — no installation needed for
   colleagues, they just need the link.

**Privacy note:** since the GitHub repo is public on the free tier, anyone
with the link can see your code and whatever sample data sits in the repo.
If your real faculty/course names are sensitive, don't upload your real
CSVs to GitHub — keep using the file-upload feature in the app so real
data only ever lives in your HOD's browser session, never on GitHub.

If your college has stricter data-privacy requirements, the safer
alternative is running it on a local department PC or your college's
internal server — the same `streamlit run app.py --server.address 0.0.0.0`
command works identically, just restrict network access to the internal
LAN only.

---

## 8. Validating before trusting it

Before relying on this for a real semester:
1. Run it on **last semester's actual data**.
2. Compare its output against what was manually decided.
3. If it disagrees, ask *why* — usually it's because a constraint you
   assumed was "obvious" (e.g. "Prof. X always teaches DBMS") was never
   encoded. Add it as a preference or a hard rule in `solver.py`.
4. Repeat until the department head is comfortable trusting it.

This iterative tuning step is normal and expected — it's not a sign the
tool is broken.
