"""
solver.py
Core constraint-programming engine for faculty-to-course-to-room allocation,
with teaching-history-aware scoring and weekly timetable generation.

Uses Google OR-Tools CP-SAT solver.

Hard constraints (never violated):
  1. Every course section gets exactly one faculty assigned.
  2. A faculty member is only assigned courses matching their specialization.
  3. No faculty exceeds their max_load (number of sections they can teach).
  4. No faculty is double-booked across two sections in the same time slot.
  5. Every course section gets exactly one room assigned.
  6. A room is only assigned to a section if its type matches what the course
     needs (e.g. "Lab" courses only go in "Lab" rooms) and its capacity is
     large enough.
  7. No room is double-booked across two sections in the same time slot.

Soft constraints (optimized, not mandatory):
  8. Maximize satisfaction of stated faculty preferences (lower rank = more preferred).
  9. Favor faculty who have taught a course before (continuity / experience bonus).
  10. Balance teaching load across faculty (penalize big gaps between busiest and idlest).

Author: prototype for college faculty-course-room-timetable allocation system.
"""

import pandas as pd
from ortools.sat.python import cp_model
from collections import defaultdict


def load_data(faculty_csv, courses_csv, preferences_csv=None, rooms_csv=None, history_csv=None):
    faculty_df = pd.read_csv(faculty_csv)
    courses_df = pd.read_csv(courses_csv)
    prefs_df = pd.read_csv(preferences_csv) if preferences_csv else pd.DataFrame(
        columns=["faculty_id", "course_id", "preference_rank"]
    )
    rooms_df = pd.read_csv(rooms_csv) if rooms_csv else pd.DataFrame(
        columns=["room_id", "room_name", "room_type", "capacity"]
    )
    history_df = pd.read_csv(history_csv) if history_csv else pd.DataFrame(
        columns=["faculty_id", "course_id", "semesters_taught", "last_taught_semester"]
    )

    # Parse specialization_tags "DBMS;AI;ML" -> set
    faculty_df["spec_set"] = faculty_df["specialization_tags"].apply(
        lambda s: set(x.strip() for x in str(s).split(";") if x.strip())
    )

    return faculty_df, courses_df, prefs_df, rooms_df, history_df


def expand_course_sections(courses_df):
    """
    Each course may need multiple sections (e.g. DBMS has 2 sections).
    Expand into individual section rows.

    Note: all sections of the same course currently share the same time_slot
    (i.e. two sections of DBMS run in parallel at the same time, taught by
    different faculty, in different rooms). If your department runs sections
    at different times, extend courses.csv with one row per section instead
    of a 'sections' count.
    """
    rows = []
    for _, row in courses_df.iterrows():
        for sec in range(1, int(row["sections"]) + 1):
            rows.append({
                "course_id": row["course_id"],
                "course_name": row["course_name"],
                "section_no": sec,
                "required_specialization": row["required_specialization"],
                "credits": row["credits"],
                "time_slot": row.get("time_slot", None),
                "room_type_needed": row.get("room_type_needed", None),
                "min_capacity": row.get("min_capacity", 0),
            })
    return pd.DataFrame(rows)


def build_preference_lookup(prefs_df):
    """Returns dict: (faculty_id, course_id) -> rank (lower = more preferred)."""
    lookup = {}
    for _, row in prefs_df.iterrows():
        lookup[(row["faculty_id"], row["course_id"])] = int(row["preference_rank"])
    return lookup


def build_history_lookup(history_df):
    """
    Returns dict: (faculty_id, course_id) -> semesters_taught (int).
    Missing pairs mean the faculty has never taught that course before -> 0.
    """
    lookup = {}
    for _, row in history_df.iterrows():
        lookup[(row["faculty_id"], row["course_id"])] = int(row["semesters_taught"])
    return lookup


def solve_allocation(faculty_df, courses_df, prefs_df, rooms_df=None, history_df=None,
                      preference_weight=10, balance_weight=5, history_weight=6,
                      time_limit_seconds=30):
    """
    Args:
        history_weight: how strongly to favor faculty who've taught a course
            before. 0 disables history-based scoring entirely. Higher values
            increasingly favor "give it to whoever already knows it" over
            preferences or fresh balance.

    Returns:
        status_str: "OPTIMAL" / "FEASIBLE" / "INFEASIBLE"
        result_df: DataFrame of assignments (empty if infeasible)
        diagnostics: dict with extra info (unassigned sections, load summary, etc.)
    """
    if rooms_df is None:
        rooms_df = pd.DataFrame(columns=["room_id", "room_name", "room_type", "capacity"])
    if history_df is None:
        history_df = pd.DataFrame(columns=["faculty_id", "course_id", "semesters_taught", "last_taught_semester"])

    sections_df = expand_course_sections(courses_df)
    pref_lookup = build_preference_lookup(prefs_df)
    history_lookup = build_history_lookup(history_df)

    model = cp_model.CpModel()

    faculty_ids = list(faculty_df["faculty_id"])
    room_ids = list(rooms_df["room_id"]) if len(rooms_df) > 0 else []
    n_sections = len(sections_df)
    has_rooms = len(room_ids) > 0

    # ========================================================================
    # FACULTY ASSIGNMENT VARIABLES: x[(faculty_id, section_idx)]
    # ========================================================================
    x = {}
    eligible_faculty_pairs = []

    for f_idx, f_row in faculty_df.iterrows():
        for s_idx, s_row in sections_df.iterrows():
            required_spec = s_row["required_specialization"]
            if required_spec in f_row["spec_set"]:
                x[(f_row["faculty_id"], s_idx)] = model.NewBoolVar(
                    f"x_{f_row['faculty_id']}_{s_idx}"
                )
                eligible_faculty_pairs.append((f_row["faculty_id"], s_idx))

    # ---- HARD CONSTRAINT 1: every section gets exactly one eligible faculty ----
    unassignable_sections = []
    for s_idx, s_row in sections_df.iterrows():
        vars_for_section = [x[(fid, s_idx)] for fid in faculty_ids if (fid, s_idx) in x]
        if not vars_for_section:
            unassignable_sections.append(s_idx)
            continue
        model.Add(sum(vars_for_section) == 1)

    # ---- HARD CONSTRAINT 2: faculty cannot exceed max_load ----
    for f_idx, f_row in faculty_df.iterrows():
        fid = f_row["faculty_id"]
        vars_for_faculty = [x[(fid, s_idx)] for s_idx in sections_df.index if (fid, s_idx) in x]
        if vars_for_faculty:
            model.Add(sum(vars_for_faculty) <= int(f_row["max_load"]))

    # ---- HARD CONSTRAINT 3: no faculty double-booked in the same time slot ----
    has_time_slots = "time_slot" in sections_df.columns and sections_df["time_slot"].notna().any()
    slot_groups = None
    if has_time_slots:
        slot_groups = sections_df.dropna(subset=["time_slot"]).groupby("time_slot")
        for slot_name, group in slot_groups:
            section_indices_in_slot = list(group.index)
            for f_idx, f_row in faculty_df.iterrows():
                fid = f_row["faculty_id"]
                vars_in_slot = [
                    x[(fid, s_idx)] for s_idx in section_indices_in_slot if (fid, s_idx) in x
                ]
                if len(vars_in_slot) > 1:
                    model.Add(sum(vars_in_slot) <= 1)

    # ========================================================================
    # ROOM ASSIGNMENT VARIABLES: y[(room_id, section_idx)]
    # ========================================================================
    y = {}
    eligible_room_pairs = []
    room_unassignable_sections = []

    if has_rooms:
        for r_idx, r_row in rooms_df.iterrows():
            for s_idx, s_row in sections_df.iterrows():
                type_needed = s_row.get("room_type_needed")
                min_cap = s_row.get("min_capacity", 0)
                type_ok = pd.isna(type_needed) or type_needed == r_row.get("room_type")
                capacity_ok = float(r_row.get("capacity", 0)) >= float(min_cap or 0)
                if type_ok and capacity_ok:
                    y[(r_row["room_id"], s_idx)] = model.NewBoolVar(
                        f"y_{r_row['room_id']}_{s_idx}"
                    )
                    eligible_room_pairs.append((r_row["room_id"], s_idx))

        # ---- HARD CONSTRAINT 5: every section gets exactly one eligible room ----
        for s_idx, s_row in sections_df.iterrows():
            vars_for_section = [y[(rid, s_idx)] for rid in room_ids if (rid, s_idx) in y]
            if not vars_for_section:
                room_unassignable_sections.append(s_idx)
                continue
            model.Add(sum(vars_for_section) == 1)

        # ---- HARD CONSTRAINT 7: no room double-booked in the same time slot ----
        if has_time_slots:
            for slot_name, group in slot_groups:
                section_indices_in_slot = list(group.index)
                for r_idx, r_row in rooms_df.iterrows():
                    rid = r_row["room_id"]
                    vars_in_slot = [
                        y[(rid, s_idx)] for s_idx in section_indices_in_slot if (rid, s_idx) in y
                    ]
                    if len(vars_in_slot) > 1:
                        model.Add(sum(vars_in_slot) <= 1)

    # ========================================================================
    # SOFT CONSTRAINTS / OBJECTIVE
    # ========================================================================
    # Preference satisfaction: rank 1 -> score 9, rank 2 -> score 8, etc.
    preference_terms = []
    for (fid, s_idx) in eligible_faculty_pairs:
        course_id = sections_df.loc[s_idx, "course_id"]
        rank = pref_lookup.get((fid, course_id))
        score = max(0, 10 - rank) if rank is not None else 0
        preference_terms.append(score * x[(fid, s_idx)])

    # Teaching history bonus: more prior semesters teaching this exact course
    # -> higher score, capped so one mega-veteran doesn't dominate everything.
    # semesters_taught of 0/1/2/3/4+ -> score 0/2/4/6/8 (capped at 8).
    history_terms = []
    for (fid, s_idx) in eligible_faculty_pairs:
        course_id = sections_df.loc[s_idx, "course_id"]
        sems = history_lookup.get((fid, course_id), 0)
        score = min(8, sems * 2)
        if score > 0:
            history_terms.append(score * x[(fid, s_idx)])

    # Workload balance: minimize the peak load any one faculty member carries.
    load_vars = {}
    for f_idx, f_row in faculty_df.iterrows():
        fid = f_row["faculty_id"]
        vars_for_faculty = [x[(fid, s_idx)] for s_idx in sections_df.index if (fid, s_idx) in x]
        load_var = model.NewIntVar(0, int(f_row["max_load"]), f"load_{fid}")
        if vars_for_faculty:
            model.Add(load_var == sum(vars_for_faculty))
        else:
            model.Add(load_var == 0)
        load_vars[fid] = load_var

    max_load_var = model.NewIntVar(0, int(faculty_df["max_load"].max()), "max_load")
    model.AddMaxEquality(max_load_var, list(load_vars.values()))

    model.Maximize(
        preference_weight * sum(preference_terms)
        + history_weight * sum(history_terms)
        - balance_weight * max_load_var
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    status_str = solver.StatusName(status)

    diagnostics = {
        "total_sections": n_sections,
        "unassignable_sections": [],
        "unassignable_rooms": [],
        "load_summary": {},
    }

    for s_idx in unassignable_sections:
        row = sections_df.loc[s_idx]
        diagnostics["unassignable_sections"].append(
            f"{row['course_name']} (Section {row['section_no']}) — "
            f"no faculty has specialization '{row['required_specialization']}'"
        )

    for s_idx in room_unassignable_sections:
        row = sections_df.loc[s_idx]
        diagnostics["unassignable_rooms"].append(
            f"{row['course_name']} (Section {row['section_no']}) — "
            f"no room matches type '{row.get('room_type_needed')}' "
            f"with capacity >= {row.get('min_capacity')}"
        )

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return status_str, pd.DataFrame(), diagnostics

    # ---- Extract solution ----
    room_for_section = {}
    if has_rooms:
        for (rid, s_idx) in eligible_room_pairs:
            if solver.Value(y[(rid, s_idx)]) == 1:
                room_for_section[s_idx] = rid

    assignments = []
    for (fid, s_idx) in eligible_faculty_pairs:
        if solver.Value(x[(fid, s_idx)]) == 1:
            s_row = sections_df.loc[s_idx]
            f_row = faculty_df[faculty_df["faculty_id"] == fid].iloc[0]
            course_id = s_row["course_id"]
            rank = pref_lookup.get((fid, course_id))
            sems_taught = history_lookup.get((fid, course_id), 0)

            room_id = room_for_section.get(s_idx, "—")
            room_name = "—"
            if room_id != "—" and len(rooms_df) > 0:
                match = rooms_df[rooms_df["room_id"] == room_id]
                if len(match) > 0:
                    room_name = match.iloc[0]["room_name"]

            assignments.append({
                "faculty_id": fid,
                "faculty_name": f_row["name"],
                "course_id": course_id,
                "course_name": s_row["course_name"],
                "section_no": s_row["section_no"],
                "time_slot": s_row.get("time_slot", "—"),
                "room_id": room_id,
                "room_name": room_name,
                "credits": s_row["credits"],
                "preference_rank": rank if rank is not None else "—",
                "times_taught_before": sems_taught,
            })

    result_df = pd.DataFrame(assignments).sort_values(["faculty_name", "course_name"])

    load_summary = defaultdict(int)
    for a in assignments:
        load_summary[a["faculty_name"]] += 1
    for _, f_row in faculty_df.iterrows():
        load_summary.setdefault(f_row["name"], 0)
    diagnostics["load_summary"] = dict(load_summary)

    return status_str, result_df, diagnostics


def build_faculty_timetable(result_df, days_order=None):
    """
    Pivot the flat result_df into a per-faculty weekly grid:
    rows = time, columns = days, cells = "Course Name (Room)".

    Assumes time_slot strings look like "Mon-9AM", "Tue-11AM" (Day-Time,
    separated by a hyphen). If your time_slot format differs, adjust the
    split logic below.

    Returns: dict {faculty_name: pivoted DataFrame}
    """
    if len(result_df) == 0:
        return {}

    df = result_df.copy()
    df["day"] = df["time_slot"].apply(lambda s: s.split("-")[0] if isinstance(s, str) and "-" in s else "—")
    df["time"] = df["time_slot"].apply(lambda s: s.split("-", 1)[1] if isinstance(s, str) and "-" in s else s)

    if days_order is None:
        days_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    present_days = [d for d in days_order if d in df["day"].unique()]
    time_order = sorted(df["time"].unique())

    timetables = {}
    for faculty_name, group in df.groupby("faculty_name"):
        grid = pd.DataFrame(index=time_order, columns=present_days)
        for _, row in group.iterrows():
            room_part = f" ({row['room_name']})" if row.get("room_name", "—") != "—" else ""
            label = f"{row['course_name']}{room_part}"
            if row["day"] in grid.columns and row["time"] in grid.index:
                grid.loc[row["time"], row["day"]] = label
        grid = grid.fillna("")
        timetables[faculty_name] = grid

    return timetables


if __name__ == "__main__":
    # Quick standalone test using the sample data
    faculty_df, courses_df, prefs_df, rooms_df, history_df = load_data(
        "sample_data/faculty.csv",
        "sample_data/courses.csv",
        "sample_data/preferences.csv",
        "sample_data/rooms.csv",
        "sample_data/teaching_history.csv",
    )
    status, result_df, diagnostics = solve_allocation(
        faculty_df, courses_df, prefs_df, rooms_df, history_df
    )
    print("Status:", status)
    print(result_df.to_string(index=False))
    print("\nLoad summary:", diagnostics["load_summary"])
    if diagnostics["unassignable_sections"]:
        print("\nWARNING — unassignable sections (faculty):")
        for msg in diagnostics["unassignable_sections"]:
            print(" -", msg)
    if diagnostics["unassignable_rooms"]:
        print("\nWARNING — unassignable sections (rooms):")
        for msg in diagnostics["unassignable_rooms"]:
            print(" -", msg)

    print("\n--- Sample faculty timetable (first faculty) ---")
    timetables = build_faculty_timetable(result_df)
    for name, grid in list(timetables.items())[:1]:
        print(f"\n{name}:")
        print(grid)
