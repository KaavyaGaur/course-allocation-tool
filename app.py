"""
app.py
Streamlit prototype UI for faculty-to-course allocation.

Run locally with:
    streamlit run app.py

Deploy free at https://streamlit.io/cloud by pushing this folder to a GitHub repo
and connecting it (no payment, generous free tier for small apps like this).
"""

import streamlit as st
import pandas as pd
from io import BytesIO

from solver import load_data, solve_allocation

st.set_page_config(page_title="Faculty Course Allocation", layout="wide")

st.title("📚 Faculty-to-Course Allocation System")
st.caption(
    "A constraint-optimization prototype — assigns faculty to course sections "
    "based on specialization match, workload limits, and stated preferences."
)

# ----------------------------------------------------------------------------
# SIDEBAR: data input
# ----------------------------------------------------------------------------
st.sidebar.header("1. Upload your data")
st.sidebar.markdown(
    "Upload your three CSV files, or leave blank to use the bundled sample data "
    "so you can try the tool immediately."
)

faculty_file = st.sidebar.file_uploader("Faculty CSV", type="csv", key="faculty")
courses_file = st.sidebar.file_uploader("Courses CSV", type="csv", key="courses")
prefs_file = st.sidebar.file_uploader("Preferences CSV (optional)", type="csv", key="prefs")

use_sample = st.sidebar.checkbox("Use bundled sample data instead", value=not faculty_file)

st.sidebar.header("2. Tune the optimization")
preference_weight = st.sidebar.slider(
    "Preference weight", 0, 20, 10,
    help="Higher = solver tries harder to satisfy faculty's preferred courses."
)
balance_weight = st.sidebar.slider(
    "Workload balance weight", 0, 20, 5,
    help="Higher = solver tries harder to spread load evenly across faculty."
)
time_limit = st.sidebar.slider("Solver time limit (seconds)", 5, 120, 30)

# ----------------------------------------------------------------------------
# Load data
# ----------------------------------------------------------------------------
def load_csv_or_default(uploaded_file, default_path):
    if uploaded_file is not None:
        return pd.read_csv(uploaded_file)
    return pd.read_csv(default_path)

try:
    if use_sample:
        faculty_df = pd.read_csv("sample_data/faculty.csv")
        courses_df = pd.read_csv("sample_data/courses.csv")
        prefs_df = pd.read_csv("sample_data/preferences.csv")
        st.info("Using bundled sample data. Upload your own CSVs in the sidebar to switch.")
    else:
        if not faculty_file or not courses_file:
            st.warning("Please upload both Faculty and Courses CSVs, or check 'Use bundled sample data'.")
            st.stop()
        faculty_df = pd.read_csv(faculty_file)
        courses_df = pd.read_csv(courses_file)
        prefs_df = pd.read_csv(prefs_file) if prefs_file else pd.DataFrame(
            columns=["faculty_id", "course_id", "preference_rank"]
        )
except Exception as e:
    st.error(f"Error reading CSV files: {e}")
    st.stop()

# Parse spec_set for display / validation (mirrors solver.py logic)
faculty_df["spec_set"] = faculty_df["specialization_tags"].apply(
    lambda s: set(x.strip() for x in str(s).split(";") if x.strip())
)

# ----------------------------------------------------------------------------
# Data preview tabs
# ----------------------------------------------------------------------------
tab1, tab2, tab3 = st.tabs(["👩‍🏫 Faculty", "📖 Courses", "⭐ Preferences"])
with tab1:
    st.dataframe(faculty_df.drop(columns=["spec_set"]), use_container_width=True)
with tab2:
    st.dataframe(courses_df, use_container_width=True)
with tab3:
    if len(prefs_df) > 0:
        st.dataframe(prefs_df, use_container_width=True)
    else:
        st.caption("No preferences provided — allocation will optimize purely on qualification and balance.")

# ----------------------------------------------------------------------------
# Basic data validation before solving
# ----------------------------------------------------------------------------
required_faculty_cols = {"faculty_id", "name", "specialization_tags", "max_load"}
required_course_cols = {"course_id", "course_name", "required_specialization", "sections", "credits"}
recommended_course_cols = {"time_slot"}

missing_f = required_faculty_cols - set(faculty_df.columns)
missing_c = required_course_cols - set(courses_df.columns)

if missing_f or missing_c:
    if missing_f:
        st.error(f"Faculty CSV is missing required columns: {missing_f}")
    if missing_c:
        st.error(f"Courses CSV is missing required columns: {missing_c}")
    st.stop()

if recommended_course_cols - set(courses_df.columns):
    st.warning(
        "No 'time_slot' column found in Courses CSV — the solver will not be able "
        "to check for faculty double-booking across simultaneous sections. "
        "Add a 'time_slot' column (e.g. 'Mon-9AM') to enable this check."
    )

# ----------------------------------------------------------------------------
# Run solver
# ----------------------------------------------------------------------------
st.header("3. Generate allocation")

if st.button("🚀 Run Allocation", type="primary"):
    with st.spinner("Solving... this usually takes a few seconds."):
        status, result_df, diagnostics = solve_allocation(
            faculty_df, courses_df, prefs_df,
            preference_weight=preference_weight,
            balance_weight=balance_weight,
            time_limit_seconds=time_limit,
        )

    if status == "INFEASIBLE":
        st.error(
            "❌ No valid allocation exists with the current data and constraints. "
            "This usually means total teaching capacity (sum of max_load) is lower "
            "than the total number of course sections that need staffing. "
            f"Total sections required: {diagnostics['total_sections']}, "
            f"total faculty capacity: {int(faculty_df['max_load'].sum())}."
        )
    elif status not in ("OPTIMAL", "FEASIBLE"):
        st.error(f"Solver returned unexpected status: {status}. Try increasing the time limit.")
    else:
        if status == "OPTIMAL":
            st.success("✅ Optimal allocation found!")
        else:
            st.warning(
                "⚠️ Found a feasible allocation, but couldn't prove it's optimal within "
                "the time limit. Try increasing the solver time limit in the sidebar."
            )

        if diagnostics["unassignable_sections"]:
            st.warning(
                "Some course sections could not be assigned to any faculty "
                "(no one has the matching specialization):"
            )
            for msg in diagnostics["unassignable_sections"]:
                st.write(f"- {msg}")

        st.subheader("📋 Final Allocation")
        st.dataframe(result_df, use_container_width=True)

        # Load summary chart
        st.subheader("⚖️ Faculty Workload Summary")
        load_df = pd.DataFrame(
            list(diagnostics["load_summary"].items()),
            columns=["Faculty", "Sections Assigned"]
        ).sort_values("Sections Assigned", ascending=False)
        st.bar_chart(load_df.set_index("Faculty"))

        # Preference satisfaction summary
        if len(prefs_df) > 0 and "preference_rank" in result_df.columns:
            matched = result_df[result_df["preference_rank"] != "—"]
            st.metric(
                "Preference matches satisfied",
                f"{len(matched)} / {len(result_df)} assignments",
            )

        # Download as Excel
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            result_df.to_excel(writer, index=False, sheet_name="Allocation")
            load_df.to_excel(writer, index=False, sheet_name="Workload Summary")
        output.seek(0)

        st.download_button(
            "⬇️ Download Allocation as Excel",
            data=output,
            file_name="faculty_course_allocation.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

st.divider()
st.caption(
    "Prototype built with Google OR-Tools (CP-SAT constraint solver) + Streamlit. "
    "Hard rules enforced: specialization match, max teaching load, one faculty per section. "
    "Soft rules optimized: preference satisfaction, workload balance."
)
