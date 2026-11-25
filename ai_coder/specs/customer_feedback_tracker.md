# Customer Feedback Tracker

## Description
Customer Feedback Tracker is a business software application that allows businesses to collect and analyze customer feedback. The software provides a user-friendly interface for businesses to create and manage feedback forms, distribute them to customers via email or social media platforms, and collect responses. It also offers data visualization tools to analyze feedback data, identify trends and patterns, and generate actionable insights for improving products and services.

## Requirements
FR1 Form management: create a feedback form with a title, description and an ordered list of questions; list, view, update (title/description/active flag) and delete forms. Form titles must be non-empty.
FR2 Question types: each question is one of "rating" (integer 1-5), "nps" (integer 0-10), "text" (free text) or "choice" (one of a list of options). Questions can be marked required. A choice question must have at least two options.
FR3 Response collection: submit a response to a form with answers keyed by question id, an optional respondent email and the channel it came from ("email", "twitter", "facebook", "linkedin", "web"). Reject responses to unknown or inactive forms, missing required answers, out-of-range ratings/NPS scores and invalid choices. Store a submission timestamp (allow passing a timestamp for imports).
FR4 Distribution by email: for a list of customer emails, build personalised invitation emails containing the form link (base URL + form id + channel tag). Validate email addresses. By default, do not send anything: save each message as an .eml file in an outbox folder. Sending through SMTP is optional and configured explicitly.
FR5 Distribution on social media: generate share links for Twitter/X, Facebook and LinkedIn that point to the form link with the correct channel tag (URL-encoded).
FR6 Persistence: store forms, questions and responses in SQLite (file path, or ":memory:" for tests). Import responses from CSV and export a form's responses to CSV.
FR7 Analytics: for a form, compute the response count; per rating question the average, count and 1-5 distribution; the NPS score (% promoters 9-10 minus % detractors 0-6); responses per channel; and a simple keyword-based sentiment (positive/neutral/negative) for text answers with overall sentiment percentages and the most frequent keywords (stop words removed).
FR8 Trends: group responses by day, week or month and report count and average rating per period, so the user can see whether satisfaction is improving or declining.
FR9 Actionable insights: generate a list of plain-English insights from the analytics, e.g. low average rating on a question (< 3), negative NPS, a declining rating trend between the first and last period, a high share of negative comments (> 30%), and the most frequent complaint keywords.
FR10 Data visualization: export charts as standalone SVG files without third-party libraries: a bar chart (e.g. rating distribution, channel breakdown) and a line chart (trend over time). Also print simple text bar charts in the terminal.
FR11 User interface: a command-line interface (`python main.py ...`) with sub-commands to create a form from a JSON file, list forms, share a form (email/social), submit a response, import/export CSV, show a report with insights, and export charts. Errors are shown as friendly messages, not tracebacks.

## Non-functional requirements
NFR1 Python 3.10+ and the standard library only (pytest for tests).
NFR2 Every module, class and function has a docstring; invalid input raises ValueError/KeyError with a clear message.
NFR3 Tests are deterministic and run offline (no real email or network).
