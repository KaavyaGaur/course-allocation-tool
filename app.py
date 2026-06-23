"""
app.py
Streamlit prototype UI for faculty-to-course-to-room allocation, with
teaching-history-aware scoring and a weekly timetable view per faculty.

Run locally with:
    streamlit run app.py

Deploy free at https://streamlit.io/cloud by pushing this folder to a GitHub repo
and connecting it (no payment, generous free tier for small apps like this).
"""

import streamlit as st
import pandas as pd
from io import BytesIO

from solver import load_data, solve_allocation, build_faculty_timetable

st.set_page_config(page_title="Faculty Course Allocation", layout="wide")

# ----------------------------------------------------------------------------
# CUSTOM CSS — layout/look tweaks that Streamlit's theme config can't do.
# Edit colors/spacing here directly; this is plain CSS.
# ----------------------------------------------------------------------------
st.markdown("""
<style>
    /* Import a clean Google Font */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');

    html, body, [class*="css"]  {
        font-family: 'Inter', sans-serif;
    }

    /* Page title */
    h1 {
        font-weight: 700 !important;
        color: #0D2C54;
        padding-bottom: 0.2rem;
    }

    /* Section headers (st.header / st.subheader) */
    h2, h3 {
        font-weight: 600 !important;
        color: #14365E;
        margin-top: 1.2rem;
    }

    /* Add breathing room around the main content block */
    .block-container {
        padding-top: 2rem;
        padding-bottom: 3rem;
        max-width: 1200px;
    }

    /* Primary buttons (Run Allocation, Download) */
    .stButton > button, .stDownloadButton > button {
        border-radius: 8px;
        font-weight: 600;
        padding: 0.5rem 1.2rem;
        border: none;
    }
    .stButton > button[kind="primary"] {
        background-color: #1565C0;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #0D47A1;
    }

    /* Sidebar background tweak + spacing */
    section[data-testid="stSidebar"] {
        padding-top: 1rem;
    }

    /* Dataframe / table corners */
    .stDataFrame {
        border-radius: 8px;
        overflow: hidden;
    }

    /* Metric cards (st.metric) get a subtle card look */
    div[data-testid="stMetric"] {
        background-color: #F0F4F8;
        padding: 1rem;
        border-radius: 10px;
        border: 1px solid #DDE5ED;
    }

    /* Tabs styling */
    .stTabs [data-baseweb="tab"] {
        font-weight: 600;
    }

    /* Timetable grid cells */
    .timetable-grid td {
        text-align: center;
        vertical-align: middle;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

st.title("📚 Faculty-to-Course-to-Room Allocation System")
st.caption(
    "A constraint-optimization prototype — assigns faculty and rooms to course "
    "sections based on specialization match, workload limits, room capacity, "
    "teaching history, and stated preferences, then generates a weekly timetable."
)

# ----------------------------------------------------------------------------
# SIDEBAR: data input
# ----------------------------------------------------------------------------
st.sidebar.header("1. Upload your data")
st.sidebar.markdown(
    "Upload your CSV files, or leave 'Use bundled sample data' checked to try "
    "the tool immediately. Faculty and Courses are required; Preferences, "
    "Rooms, and Teaching History are optional but recommended."
)

faculty_file = st.sidebar.file_uploader("Faculty CSV", type="csv", key="faculty")
courses_file = st.sidebar.file_uploader("Courses CSV", type="csv", key="courses")
prefs_file = st.sidebar.file_uploader("Preferences CSV (optional)", type="csv", key="prefs")
rooms_file = st.sidebar.file_uploader("Rooms CSV (optional)", type="csv", key="rooms")
history_file = st.sidebar.file_uploader("Teaching History CSV (optional)", type="csv", key="history")

use_sample = st.sidebar.checkbox("Use bundled sample data instead", value=not faculty_file)

st.sidebar.header("2. Tune the optimization")
preference_weight = st.sidebar.slider(
    "Preference weight", 0, 20, 10,
    help="Higher = solver tries harder to satisfy faculty's preferred courses."
)
history_weight = st.sidebar.slider(
    "Teaching history weight", 0, 20, 6,
    help="Higher = solver favors giving a course to faculty who have taught "
         "it before (continuity/experience). Set to 0 to ignore history entirely."
)
balance_weight = st.sidebar.slider(
    "Workload balance weight", 0, 20, 5,
    help="Higher = solver tries harder to spread load evenly across faculty."
)
time_limit = st.sidebar.slider("Solver time limit (seconds)", 5, 120, 30)

# ----------------------------------------------------------------------------
# Load data
# ----------------------------------------------------------------------------
try:
    if use_sample:
        faculty_df = pd.read_csv("sample_data/faculty.csv")
        courses_df = pd.read_csv("sample_data/courses.csv")
        prefs_df = pd.read_csv("sample_data/preferences.csv")
        rooms_df = pd.read_csv("sample_data/rooms.csv")
        history_df = pd.read_csv("sample_data/teaching_history.csv")
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
        rooms_df = pd.read_csv(rooms_file) if rooms_file else pd.DataFrame(
            columns=["room_id", "room_name", "room_type", "capacity"]
        )
        history_df = pd.read_csv(history_file) if history_file else pd.DataFrame(
            columns=["faculty_id", "course_id", "semesters_taught", "last_taught_semester"]
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
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["👩‍🏫 Faculty", "📖 Courses", "⭐ Preferences", "🏫 Rooms", "🕓 Teaching History"]
)
with tab1:
    st.dataframe(faculty_df.drop(columns=["spec_set"]), use_container_width=True)
with tab2:
    st.dataframe(courses_df, use_container_width=True)
with tab3:
    if len(prefs_df) > 0:
        st.dataframe(prefs_df, use_container_width=True)
    else:
        st.caption("No preferences provided — allocation will optimize purely on qualification and balance.")
with tab4:
    if len(rooms_df) > 0:
        st.dataframe(rooms_df, use_container_width=True)
    else:
        st.caption("No rooms provided — allocation will skip room assignment entirely.")
with tab5:
    if len(history_df) > 0:
        st.dataframe(history_df, use_container_width=True)
    else:
        st.caption("No teaching history provided — allocation will ignore prior-experience scoring.")

# ----------------------------------------------------------------------------
# Basic data validation before solving
# ----------------------------------------------------------------------------
required_faculty_cols = {"faculty_id", "name", "specialization_tags", "max_load"}
required_course_cols = {"course_id", "course_name", "required_specialization", "sections", "credits"}
recommended_course_cols = {"time_slot"}
room_aware_course_cols = {"room_type_needed", "min_capacity"}

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
        "No 'time_slot' column found in Courses CSV — the solver cannot check "
        "for faculty or room double-booking. Add a 'time_slot' column "
        "(e.g. 'Mon-9AM') to enable this check."
    )

if len(rooms_df) > 0 and (room_aware_course_cols - set(courses_df.columns)):
    st.warning(
        "Rooms CSV was provided, but Courses CSV is missing 'room_type_needed' "
        "and/or 'min_capacity' columns — room assignment will be skipped. Add "
        "these columns to enable automatic room allocation."
    )

# ----------------------------------------------------------------------------
# Run solver
# ----------------------------------------------------------------------------
st.header("3. Generate allocation")

if st.button("🚀 Run Allocation", type="primary"):
    with st.spinner("Solving... this usually takes a few seconds."):
        status, result_df, diagnostics = solve_allocation(
            faculty_df, courses_df, prefs_df,
            rooms_df=rooms_df if len(rooms_df) > 0 else None,
            history_df=history_df if len(history_df) > 0 else None,
            preference_weight=preference_weight,
            history_weight=history_weight,
            balance_weight=balance_weight,
            time_limit_seconds=time_limit,
        )

    if status == "INFEASIBLE":
        st.error(
            "❌ No valid allocation exists with the current data and constraints. "
            "This usually means total teaching capacity (sum of max_load) is lower "
            "than the total number of course sections that need staffing, or rooms "
            "are too scarce for the number of simultaneous sections. "
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
            st.warning("Some sections could not be assigned a faculty (no qualification match):")
            for msg in diagnostics["unassignable_sections"]:
                st.write(f"- {msg}")

        if diagnostics.get("unassignable_rooms"):
            st.warning("Some sections could not be assigned a room (no matching type/capacity):")
            for msg in diagnostics["unassignable_rooms"]:
                st.write(f"- {msg}")

        st.subheader("📋 Final Allocation")
        st.dataframe(result_df, use_container_width=True)

        # ---- Per-faculty weekly timetable grid ----
        st.subheader("🗓️ Weekly Timetable — per Faculty")
        st.caption(
            "Generated from the time_slot values in your Courses CSV. "
            "Format assumed: 'Day-Time', e.g. 'Mon-9AM'."
        )
        timetables = build_faculty_timetable(result_df)
        if timetables:
            faculty_names = sorted(timetables.keys())
            selected_faculty = st.selectbox("Select faculty to view their timetable:", faculty_names)
            st.dataframe(timetables[selected_faculty], use_container_width=True)

            with st.expander("View all faculty timetables at once"):
                for name in faculty_names:
                    st.markdown(f"**{name}**")
                    st.dataframe(timetables[name], use_container_width=True)
        else:
            st.caption("No time_slot data available to build a timetable.")

        # Load summary chart
        st.subheader("⚖️ Faculty Workload Summary")
        load_df = pd.DataFrame(
            list(diagnostics["load_summary"].items()),
            columns=["Faculty", "Sections Assigned"]
        ).sort_values("Sections Assigned", ascending=False)
        st.bar_chart(load_df.set_index("Faculty"))

        # Preference + history satisfaction summary
        col1, col2 = st.columns(2)
        with col1:
            if len(prefs_df) > 0 and "preference_rank" in result_df.columns:
                matched = result_df[result_df["preference_rank"] != "—"]
                st.metric(
                    "Preference matches satisfied",
                    f"{len(matched)} / {len(result_df)} assignments",
                )
        with col2:
            if len(history_df) > 0 and "times_taught_before" in result_df.columns:
                experienced = result_df[result_df["times_taught_before"] > 0]
                st.metric(
                    "Assignments to faculty with prior experience",
                    f"{len(experienced)} / {len(result_df)} assignments",
                )

        # Download as Excel (allocation + workload + one sheet per faculty timetable)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            result_df.to_excel(writer, index=False, sheet_name="Allocation")
            load_df.to_excel(writer, index=False, sheet_name="Workload Summary")
            for name, grid in timetables.items():
                safe_name = name[:25].replace("/", "-")  # Excel sheet name limits
                grid.to_excel(writer, sheet_name=f"TT-{safe_name}")
        output.seek(0)

        st.download_button(
            "⬇️ Download Allocation + Timetables as Excel",
            data=output,
            file_name="faculty_course_room_allocation.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

st.divider()
st.caption(
    "Prototype built with Google OR-Tools (CP-SAT constraint solver) + Streamlit. "
    "Hard rules enforced: specialization match, max teaching load, one faculty "
    "per section, no faculty/room double-booking, room type & capacity match. "
    "Soft rules optimized: preference satisfaction, teaching-history continuity, "
    "workload balance."
)
