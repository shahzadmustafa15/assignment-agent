from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.web.api import router as api_router
from app.web.admin_api import router as admin_router


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


app = FastAPI(
    title="Assignment Agent Dashboard",
    description=(
        "Local dashboard for monitoring assignments, "
        "students, notifications, and system health."
    ),
    version="1.0.0",
)


# API routes
app.include_router(api_router)
app.include_router(admin_router)


# Static files
if STATIC_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "assignment-agent-dashboard",
    }


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """
<!doctype html>
<html lang="en">

<head>
    <meta charset="utf-8">

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >

    <title>Assignment Agent</title>

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            font-family:
                Inter,
                Arial,
                Helvetica,
                sans-serif;
            background: #f4f6f8;
            color: #111827;
        }

        .topbar {
            background: #111827;
            color: white;
            padding: 22px 32px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .brand {
            font-size: 22px;
            font-weight: 700;
        }

        .subtitle {
            margin-top: 4px;
            font-size: 13px;
            color: #cbd5e1;
        }

        .status {
            font-size: 13px;
            padding: 8px 12px;
            background: #1f2937;
            border-radius: 999px;
        }

        .container {
            max-width: 1250px;
            margin: 0 auto;
            padding: 28px 20px;
        }

        .controls {
            display: flex;
            gap: 12px;
            align-items: center;
            margin-bottom: 24px;
            flex-wrap: wrap;
        }

        select {
            padding: 11px 14px;
            border: 1px solid #d1d5db;
            border-radius: 8px;
            background: white;
            font-size: 14px;
        }

        button {
            border: 0;
            border-radius: 8px;
            padding: 11px 15px;
            cursor: pointer;
            background: #111827;
            color: white;
            font-weight: 600;
        }

        button:hover {
            opacity: 0.92;
        }

        .grid {
            display: grid;
            grid-template-columns:
                repeat(auto-fit, minmax(170px, 1fr));
            gap: 16px;
            margin-bottom: 28px;
        }

        .card {
            background: white;
            border-radius: 14px;
            padding: 20px;
            box-shadow:
                0 1px 3px rgba(0, 0, 0, 0.08);
        }

        .metric-label {
            color: #6b7280;
            font-size: 13px;
            margin-bottom: 8px;
        }

        .metric-value {
            font-size: 28px;
            font-weight: 700;
        }

        h2 {
            font-size: 18px;
            margin-top: 0;
            margin-bottom: 16px;
        }

        .section {
            margin-bottom: 28px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        th {
            text-align: left;
            font-size: 12px;
            color: #6b7280;
            text-transform: uppercase;
            padding: 10px 8px;
            border-bottom: 1px solid #e5e7eb;
        }

        td {
            padding: 13px 8px;
            border-bottom: 1px solid #f1f5f9;
            font-size: 14px;
        }

        .empty {
            color: #6b7280;
            font-size: 14px;
        }

        .badge {
            display: inline-block;
            padding: 5px 8px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
            background: #f3f4f6;
        }

        .loading {
            color: #6b7280;
        }

        .error {
            color: #b91c1c;
        }

        .admin-link {
            text-decoration: none;
        }

        @media (max-width: 700px) {
            .topbar {
                padding: 18px;
                align-items: flex-start;
                gap: 12px;
            }

            .container {
                padding: 20px 12px;
            }

            table {
                font-size: 12px;
            }
        }
    </style>
</head>

<body>

<div class="topbar">
    <div>
        <div class="brand">
            Assignment Agent
        </div>

        <div class="subtitle">
            Academic Assignment Monitoring System
        </div>
    </div>

    <div class="status">
        System Online
    </div>
</div>

<div class="container">

    <div class="controls">

        <select id="studentSelect">
            <option value="">
                Loading students...
            </option>
        </select>

        <button onclick="loadDashboard()">
            Refresh
        </button>

        <button onclick="loadNotifications()">
            Notification Audit
        </button>

        <a
            class="admin-link"
            href="/admin"
        >
            <button type="button">
                Admin Health
            </button>
        </a>

    </div>

    <div id="metrics" class="grid">
        <div class="card loading">
            Loading dashboard...
        </div>
    </div>

    <div class="section card">
        <h2>Upcoming Deadlines</h2>

        <div id="upcoming">
            <span class="loading">
                Loading...
            </span>
        </div>
    </div>

    <div class="section card">
        <h2>Course Overview</h2>

        <div id="courses">
            <span class="loading">
                Loading...
            </span>
        </div>
    </div>

    <div class="section card">
        <h2>Recent Notifications</h2>

        <div id="notifications">
            <span class="loading">
                Loading...
            </span>
        </div>
    </div>

</div>


<script>

async function fetchJSON(url) {
    const response = await fetch(url);

    if (!response.ok) {
        throw new Error(
            `Request failed: ${response.status}`
        );
    }

    return await response.json();
}


function escapeHTML(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function formatDate(value) {
    if (!value) {
        return "Unknown";
    }

    const date = new Date(value);

    return date.toLocaleString(
        undefined,
        {
            dateStyle: "medium",
            timeStyle: "short",
        }
    );
}


async function loadStudents() {
    const students = await fetchJSON(
        "/api/students"
    );

    const select = document.getElementById(
        "studentSelect"
    );

    select.innerHTML = "";

    for (const student of students) {

        const option = document.createElement(
            "option"
        );

        option.value = student.id;

        option.textContent =
            student.name +
            (
                student.enabled
                ? ""
                : " (disabled)"
            );

        select.appendChild(option);
    }

    if (students.length > 0) {
        await loadDashboard();
    } else {
        document.getElementById(
            "metrics"
        ).innerHTML = `
            <div class="card">
                No students found.
            </div>
        `;
    }
}


async function loadDashboard() {
    const select = document.getElementById(
        "studentSelect"
    );

    const studentId = select.value;

    if (!studentId) {
        return;
    }

    try {

        const data = await fetchJSON(
            `/api/dashboard/${encodeURIComponent(
                studentId
            )}`
        );

        renderMetrics(
            data.counts
        );

        renderUpcoming(
            data.upcoming
        );

        renderCourses(
            data.courses
        );

        await loadNotifications();

    } catch (error) {

        console.error(error);

        document.getElementById(
            "metrics"
        ).innerHTML = `
            <div class="card error">
                Dashboard failed to load.
            </div>
        `;
    }
}


function renderMetrics(counts) {

    const metrics = [
        [
            "Total Assignments",
            counts.total
        ],
        [
            "Submitted",
            counts.submitted
        ],
        [
            "Pending",
            counts.pending
        ],
        [
            "Overdue",
            counts.overdue
        ],
        [
            "Due Today",
            counts.due_today
        ],
        [
            "Due Soon",
            counts.due_soon
        ],
    ];

    document.getElementById(
        "metrics"
    ).innerHTML = metrics.map(
        ([label, value]) => `
            <div class="card">

                <div class="metric-label">
                    ${escapeHTML(label)}
                </div>

                <div class="metric-value">
                    ${escapeHTML(value)}
                </div>

            </div>
        `
    ).join("");
}


function renderUpcoming(items) {

    const container = document.getElementById(
        "upcoming"
    );

    if (!items.length) {

        container.innerHTML = `
            <div class="empty">
                No upcoming deadlines.
            </div>
        `;

        return;
    }

    container.innerHTML = `
        <table>

            <thead>
                <tr>
                    <th>Course</th>
                    <th>Assignment</th>
                    <th>Deadline</th>
                    <th>Status</th>
                </tr>
            </thead>

            <tbody>

                ${items.map(
                    item => `
                        <tr>

                            <td>
                                ${escapeHTML(
                                    item.course
                                )}
                            </td>

                            <td>
                                ${escapeHTML(
                                    item.title
                                )}
                            </td>

                            <td>
                                ${escapeHTML(
                                    formatDate(
                                        item.deadline
                                    )
                                )}
                            </td>

                            <td>
                                <span class="badge">
                                    ${escapeHTML(
                                        item.status
                                    )}
                                </span>
                            </td>

                        </tr>
                    `
                ).join("")}

            </tbody>

        </table>
    `;
}


function renderCourses(courses) {

    const container = document.getElementById(
        "courses"
    );

    if (!courses.length) {

        container.innerHTML = `
            <div class="empty">
                No courses found.
            </div>
        `;

        return;
    }

    container.innerHTML = `
        <table>

            <thead>
                <tr>
                    <th>Course</th>
                    <th>Total</th>
                    <th>Submitted</th>
                    <th>Pending</th>
                    <th>Overdue</th>
                </tr>
            </thead>

            <tbody>

                ${courses.map(
                    course => `
                        <tr>

                            <td>
                                ${escapeHTML(
                                    course.course
                                )}
                            </td>

                            <td>
                                ${course.total}
                            </td>

                            <td>
                                ${course.submitted}
                            </td>

                            <td>
                                ${course.pending}
                            </td>

                            <td>
                                ${course.overdue}
                            </td>

                        </tr>
                    `
                ).join("")}

            </tbody>

        </table>
    `;
}


async function loadNotifications() {

    const select = document.getElementById(
        "studentSelect"
    );

    const studentId = select.value;

    if (!studentId) {
        return;
    }

    try {

        const rows = await fetchJSON(
            "/api/notifications?limit=100"
        );

        const filtered = rows.filter(
            row =>
                row.student_id === studentId
        );

        const container =
            document.getElementById(
                "notifications"
            );

        if (!filtered.length) {

            container.innerHTML = `
                <div class="empty">
                    No notification history available.
                </div>
            `;

            return;
        }

        container.innerHTML = `
            <table>

                <thead>
                    <tr>
                        <th>Type</th>
                        <th>Assignment</th>
                        <th>State</th>
                        <th>Time</th>
                    </tr>
                </thead>

                <tbody>

                    ${filtered
                        .slice(0, 15)
                        .map(
                            row => `
                                <tr>

                                    <td>
                                        ${escapeHTML(
                                            row.event_type
                                        )}
                                    </td>

                                    <td>
                                        ${escapeHTML(
                                            row.assignment_title
                                            || "System"
                                        )}
                                    </td>

                                    <td>
                                        <span class="badge">
                                            ${escapeHTML(
                                                row.state
                                            )}
                                        </span>
                                    </td>

                                    <td>
                                        ${escapeHTML(
                                            formatDate(
                                                row.sent_at
                                                || row.attempted_at
                                                || row.created_at
                                            )
                                        )}
                                    </td>

                                </tr>
                            `
                        )
                        .join("")}

                </tbody>

            </table>
        `;

    } catch (error) {

        console.error(error);

        document.getElementById(
            "notifications"
        ).innerHTML = `
            <div class="error">
                Failed to load notification history.
            </div>
        `;
    }
}


document.getElementById(
    "studentSelect"
).addEventListener(
    "change",
    loadDashboard
);


loadStudents().catch(
    error => {

        console.error(error);

        document.getElementById(
            "metrics"
        ).innerHTML = `
            <div class="card error">
                Dashboard failed to load.
            </div>
        `;
    }
);

</script>

</body>
</html>
"""


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard():
    return """
<!doctype html>
<html lang="en">

<head>

    <meta charset="utf-8">

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >

    <title>Assignment Agent Admin</title>

    <style>

        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            font-family:
                Inter,
                Arial,
                Helvetica,
                sans-serif;
            background: #f4f6f8;
            color: #111827;
        }

        .topbar {
            background: #111827;
            color: white;
            padding: 22px 32px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .brand {
            font-size: 22px;
            font-weight: 700;
        }

        .subtitle {
            color: #cbd5e1;
            font-size: 13px;
            margin-top: 4px;
        }

        .container {
            max-width: 1250px;
            margin: 0 auto;
            padding: 28px 20px;
        }

        .controls {
            display: flex;
            gap: 12px;
            margin-bottom: 24px;
        }

        button {
            border: 0;
            border-radius: 8px;
            padding: 11px 15px;
            cursor: pointer;
            background: #111827;
            color: white;
            font-weight: 600;
        }

        .grid {
            display: grid;
            grid-template-columns:
                repeat(
                    auto-fit,
                    minmax(180px, 1fr)
                );
            gap: 16px;
            margin-bottom: 28px;
        }

        .card {
            background: white;
            border-radius: 14px;
            padding: 20px;
            box-shadow:
                0 1px 3px rgba(0,0,0,.08);
        }

        .label {
            font-size: 13px;
            color: #6b7280;
        }

        .value {
            margin-top: 7px;
            font-size: 27px;
            font-weight: 700;
        }

        h2 {
            font-size: 18px;
            margin-top: 0;
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        th {
            text-align: left;
            padding: 10px 8px;
            font-size: 12px;
            color: #6b7280;
            text-transform: uppercase;
            border-bottom: 1px solid #e5e7eb;
        }

        td {
            padding: 12px 8px;
            border-bottom: 1px solid #f1f5f9;
            font-size: 14px;
        }

        .badge {
            display: inline-block;
            padding: 5px 8px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
            background: #f3f4f6;
        }

        .warning {
            padding: 12px;
            border-radius: 8px;
            background: #fff7ed;
            margin-bottom: 10px;
        }

        .healthy {
            color: #166534;
        }

        .problem {
            color: #b91c1c;
        }

        .section {
            margin-bottom: 28px;
        }

        a {
            text-decoration: none;
        }

    </style>

</head>

<body>

<div class="topbar">

    <div>
        <div class="brand">
            Assignment Agent Admin
        </div>

        <div class="subtitle">
            System Health & Monitoring
        </div>
    </div>

</div>


<div class="container">

    <div class="controls">

        <button onclick="loadHealth()">
            Refresh
        </button>

        <a href="/">
            <button type="button">
                Student Dashboard
            </button>
        </a>

    </div>


    <div id="metrics" class="grid">

        <div class="card">
            Loading...
        </div>

    </div>


    <div class="section card">

        <h2>
            Students
        </h2>

        <div id="students">
            Loading...
        </div>

    </div>


    <div class="section card">

        <h2>
            Timers
        </h2>

        <div id="timers">
            Loading...
        </div>

    </div>


    <div class="section card">

        <h2>
            Attention Required
        </h2>

        <div id="warnings">
            Loading...
        </div>

    </div>

</div>


<script>

function escapeHTML(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function formatDate(value) {

    if (!value) {
        return "Never";
    }

    return new Date(
        value
    ).toLocaleString();
}


async function loadHealth() {

    const response = await fetch(
        "/api/admin/health"
    );

    if (!response.ok) {
        throw new Error(
            "Admin health API failed."
        );
    }

    const data = await response.json();

    renderMetrics(data);
    renderStudents(data.students);
    renderTimers(data.timers);
    renderWarnings(data.warnings);
}


function renderMetrics(data) {

    const students =
        data.students || [];

    const totalAssignments =
        students.reduce(
            (sum, student) =>
                sum +
                student.assignments.total,
            0
        );

    const overdue =
        students.reduce(
            (sum, student) =>
                sum +
                student.assignments.overdue,
            0
        );

    const metrics = [
        [
            "Students",
            students.length
        ],
        [
            "Assignments",
            totalAssignments
        ],
        [
            "Overdue",
            overdue
        ],
        [
            "Emails Sent",
            data.notifications.sent
        ],
        [
            "Failed Emails",
            data.notifications.failed
        ],
        [
            "Pending Emails",
            data.notifications.pending
        ],
    ];

    document.getElementById(
        "metrics"
    ).innerHTML = metrics.map(
        item => `
            <div class="card">

                <div class="label">
                    ${escapeHTML(item[0])}
                </div>

                <div class="value">
                    ${escapeHTML(item[1])}
                </div>

            </div>
        `
    ).join("");
}


function renderStudents(students) {

    const container =
        document.getElementById(
            "students"
        );

    if (!students.length) {

        container.innerHTML =
            "No students found.";

        return;
    }

    container.innerHTML = `
        <table>

            <thead>
                <tr>
                    <th>Student</th>
                    <th>Status</th>
                    <th>Assignments</th>
                    <th>Pending</th>
                    <th>Overdue</th>
                    <th>Last Scan</th>
                    <th>Last Email</th>
                </tr>
            </thead>

            <tbody>

                ${students.map(
                    student => `
                        <tr>

                            <td>
                                ${escapeHTML(
                                    student.name
                                )}
                            </td>

                            <td>
                                <span class="badge">
                                    ${escapeHTML(
                                        student.scan_status
                                    )}
                                </span>
                            </td>

                            <td>
                                ${student.assignments.total}
                            </td>

                            <td>
                                ${student.assignments.pending}
                            </td>

                            <td>
                                ${student.assignments.overdue}
                            </td>

                            <td>
                                ${escapeHTML(
                                    formatDate(
                                        student.last_scan
                                    )
                                )}
                            </td>

                            <td>
                                ${escapeHTML(
                                    formatDate(
                                        student.last_email
                                    )
                                )}
                            </td>

                        </tr>
                    `
                ).join("")}

            </tbody>

        </table>
    `;
}


function renderTimers(timers) {

    const container =
        document.getElementById(
            "timers"
        );

    const rows = Object.entries(
        timers
    );

    container.innerHTML = `
        <table>

            <thead>
                <tr>
                    <th>Service</th>
                    <th>Status</th>
                </tr>
            </thead>

            <tbody>

                ${rows.map(
                    ([name, state]) => `
                        <tr>

                            <td>
                                ${escapeHTML(name)}
                            </td>

                            <td>
                                <span class="badge">
                                    ${escapeHTML(state)}
                                </span>
                            </td>

                        </tr>
                    `
                ).join("")}

            </tbody>

        </table>
    `;
}


function renderWarnings(warnings) {

    const container =
        document.getElementById(
            "warnings"
        );

    if (!warnings.length) {

        container.innerHTML = `
            <div class="healthy">
                No system warnings.
            </div>
        `;

        return;
    }

    container.innerHTML = warnings.map(
        warning => `
            <div class="warning">
                ${escapeHTML(warning)}
            </div>
        `
    ).join("");
}


loadHealth().catch(
    error => {

        console.error(error);

        document.getElementById(
            "warnings"
        ).innerHTML = `
            <div class="problem">
                Failed to load system health.
            </div>
        `;
    }
);

</script>

</body>

</html>
"""
