"""
solver.py
Core constraint-programming engine for faculty-to-course allocation.

Uses Google OR-Tools CP-SAT solver.

Hard constraints (never violated):
  1. Every course section gets exactly one faculty assigned.
  2. A faculty member is only assigned courses matching their specialization.
  3. No faculty exceeds their max_load (number of sections they can teach).

Soft constraints (optimized, not mandatory):
  4. Maximize satisfaction of stated preferences (lower rank = more preferred).
  5. Balance load across faculty (penalize big gaps between busiest and idlest).

Author: prototype for college faculty-course allocation system.
"""

import pandas as pd
from ortools.sat.python import cp_model
from collections import defaultdict


def load_data(faculty_csv, courses_csv, preferences_csv=None):
    faculty_df = pd.read_csv(faculty_csv)
    courses_df = pd.read_csv(courses_csv)
    prefs_df = pd.read_csv(preferences_csv) if preferences_csv else pd.DataFrame(
        columns=["faculty_id", "course_id", "preference_rank"]
    )

    # Parse specialization_tags "DBMS;AI;ML" -> set
    faculty_df["spec_set"] = faculty_df["specialization_tags"].apply(
        lambda s: set(x.strip() for x in str(s).split(";") if x.strip())
    )

    return faculty_df, courses_df, prefs_df


def expand_course_sections(courses_df):
    """
    Each course may need multiple sections (e.g. DBMS has 2 sections).
    Expand into individual section rows: (course_id, section_no, required_specialization, time_slot).

    Note: all sections of the same course currently share the same time_slot
    (i.e. two sections of DBMS run in parallel at the same time, taught by
    different faculty). If your department runs sections at different times,
    extend courses.csv with one row per section instead of a 'sections' count.
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
            })
    return pd.DataFrame(rows)


def build_preference_lookup(prefs_df):
    """
    Returns dict: (faculty_id, course_id) -> rank (lower = more preferred).
    Missing pairs are treated as neutral (no preference).
    """
    lookup = {}
    for _, row in prefs_df.iterrows():
        lookup[(row["faculty_id"], row["course_id"])] = int(row["preference_rank"])
    return lookup


def solve_allocation(faculty_df, courses_df, prefs_df, preference_weight=10, balance_weight=5,
                      time_limit_seconds=30):
    """
    Returns:
        status_str: "OPTIMAL" / "FEASIBLE" / "INFEASIBLE"
        result_df: DataFrame of assignments (empty if infeasible)
        diagnostics: dict with extra info (unassigned sections, load summary, etc.)
    """
    sections_df = expand_course_sections(courses_df)
    pref_lookup = build_preference_lookup(prefs_df)

    model = cp_model.CpModel()

    faculty_ids = list(faculty_df["faculty_id"])
    n_sections = len(sections_df)

    # Decision variables: x[f][s] = 1 if faculty f assigned to section s
    x = {}
    eligible_pairs = []  # track which (f, s) pairs are even allowed (qualification match)

    for f_idx, f_row in faculty_df.iterrows():
        for s_idx, s_row in sections_df.iterrows():
            required_spec = s_row["required_specialization"]
            if required_spec in f_row["spec_set"]:
                x[(f_row["faculty_id"], s_idx)] = model.NewBoolVar(
                    f"x_{f_row['faculty_id']}_{s_idx}"
                )
                eligible_pairs.append((f_row["faculty_id"], s_idx))

    # ---- HARD CONSTRAINT 1: every section gets exactly one eligible faculty ----
    # (if zero eligible faculty exist for a section, this becomes infeasible —
    #  diagnostics will catch that case separately before solving)
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
    # Group sections by time_slot; within each slot, a faculty member can be
    # assigned to at most one section (across any courses sharing that slot).
    has_time_slots = "time_slot" in sections_df.columns and sections_df["time_slot"].notna().any()
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

    # ---- SOFT CONSTRAINT: maximize preference satisfaction ----
    # Lower rank = more preferred. Convert to a score: rank 1 -> high score, rank 2 -> lower, etc.
    # No stated preference -> neutral score of 0.
    preference_terms = []
    for (fid, s_idx) in eligible_pairs:
        course_id = sections_df.loc[s_idx, "course_id"]
        rank = pref_lookup.get((fid, course_id))
        if rank is not None:
            score = max(0, 10 - rank)  # rank 1 -> 9, rank 2 -> 8, etc.
        else:
            score = 0
        preference_terms.append(score * x[(fid, s_idx)])

    # ---- SOFT CONSTRAINT: balance load across faculty ----
    # Minimize the max load any single faculty member carries (discourages overloading
    # a few people while others sit idle), weighted against preference satisfaction.
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

    # ---- OBJECTIVE: maximize preferences, minimize peak load (weighted) ----
    model.Maximize(
        preference_weight * sum(preference_terms) - balance_weight * max_load_var
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    status_str = solver.StatusName(status)

    diagnostics = {
        "total_sections": n_sections,
        "unassignable_sections": [],
        "load_summary": {},
    }

    if unassignable_sections:
        for s_idx in unassignable_sections:
            row = sections_df.loc[s_idx]
            diagnostics["unassignable_sections"].append(
                f"{row['course_name']} (Section {row['section_no']}) — "
                f"no faculty has specialization '{row['required_specialization']}'"
            )

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return status_str, pd.DataFrame(), diagnostics

    # ---- Extract solution ----
    assignments = []
    for (fid, s_idx) in eligible_pairs:
        if solver.Value(x[(fid, s_idx)]) == 1:
            s_row = sections_df.loc[s_idx]
            f_row = faculty_df[faculty_df["faculty_id"] == fid].iloc[0]
            course_id = s_row["course_id"]
            rank = pref_lookup.get((fid, course_id))
            assignments.append({
                "faculty_id": fid,
                "faculty_name": f_row["name"],
                "course_id": course_id,
                "course_name": s_row["course_name"],
                "section_no": s_row["section_no"],
                "time_slot": s_row.get("time_slot", "—"),
                "credits": s_row["credits"],
                "preference_rank": rank if rank is not None else "—",
            })

    result_df = pd.DataFrame(assignments).sort_values(["faculty_name", "course_name"])

    # Load summary per faculty
    load_summary = defaultdict(int)
    for a in assignments:
        load_summary[a["faculty_name"]] += 1
    # include zero-load faculty too
    for _, f_row in faculty_df.iterrows():
        load_summary.setdefault(f_row["name"], 0)
    diagnostics["load_summary"] = dict(load_summary)

    return status_str, result_df, diagnostics


if __name__ == "__main__":
    # Quick standalone test using the sample data
    faculty_df, courses_df, prefs_df = load_data(
        "sample_data/faculty.csv",
        "sample_data/courses.csv",
        "sample_data/preferences.csv",
    )
    status, result_df, diagnostics = solve_allocation(faculty_df, courses_df, prefs_df)
    print("Status:", status)
    print(result_df.to_string(index=False))
    print("\nLoad summary:", diagnostics["load_summary"])
    if diagnostics["unassignable_sections"]:
        print("\nWARNING — unassignable sections:")
        for msg in diagnostics["unassignable_sections"]:
            print(" -", msg)
