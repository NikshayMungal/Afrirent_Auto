import os
import pandas as pd
import streamlit as st
from datetime import datetime
import io
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# ReportLab imports for PDF generation
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

st.title("Afrirent Depot Admin Controller Automation")
st.write(
    "Welcome! Manage records, generate branded PDF summaries, and email reports directly to management."
)

EXCEL_FILE = "depot_records.xlsx"

def load_data():
    if os.path.exists(EXCEL_FILE):
        return pd.read_excel(EXCEL_FILE)
    else:
        return pd.DataFrame(
            columns=["Record ID", "Item / Description", "Quantity", "Date Added"]
        )

df = load_data()

# Create Tabs
tab1, tab2, tab3 = st.tabs(
    ["Capture Record", "View & Search Records", "Generate & Email Report"]
)

with tab1:
    st.subheader("Capture New Depot Record")
    with st.form("data_entry_form"):
        record_id = st.text_input("Record ID / Item Code")
        description = st.text_input("Item Description / Category")
        quantity = st.number_input("Quantity", min_value=0, value=1)

        submitted = st.form_submit_button("Save Record")

        if submitted:
            if record_id:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                new_data = pd.DataFrame([{
                    "Record ID": record_id,
                    "Item / Description": description,
                    "Quantity": quantity,
                    "Date Added": current_time,
                }])
                df = pd.concat([df, new_data], ignore_index=True)
                df.to_excel(EXCEL_FILE, index=False)
                st.success(f"Successfully saved record: {record_id} to Excel!")
                st.rerun()
            else:
                st.error("Please enter a valid Record ID / Item Code.")

with tab2:
    st.subheader("Search and Manage Depot Records")

    if not df.empty:
        search_query = st.text_input("🔍 Search by Record ID or Description", "")

        if search_query:
            filtered_df = df[
                df["Record ID"].astype(str).str.contains(search_query, case=False, na=False)
                | df["Item / Description"].astype(str).str.contains(search_query, case=False, na=False)
            ]
        else:
            filtered_df = df

        st.dataframe(filtered_df, use_container_width=True)

        st.markdown("---")
        st.subheader("Manage Database")
        record_to_delete = st.selectbox(
            "Select a Record ID to Delete",
            options=["Select..."] + list(df["Record ID"].astype(str)),
        )

        if record_to_delete != "Select...":
            if st.button("Delete Selected Record", type="primary"):
                df = df[df["Record ID"].astype(str) != record_to_delete]
                df.to_excel(EXCEL_FILE, index=False)
                st.success(f"Record {record_to_delete} has been deleted.")
                st.rerun()

        st.markdown("---")
        with open(EXCEL_FILE, "rb") as f:
            st.download_button(
                label="Download Full Excel Sheet",
                data=f,
                file_name="depot_records.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    else:
        st.info("No records captured yet. Use the 'Capture Record' tab to start.")

with tab3:
    st.subheader("Branded PDF Report Generator & Email Center")

    if not df.empty:
        total_records = len(df)
        total_items = df["Quantity"].sum() if "Quantity" in df.columns else 0
        report_date = datetime.now().strftime("%Y-%m-%d")

        # Function to generate professional PDF bytes using ReportLab
        def generate_pdf():
            buffer = io.BytesIO()
            doc = SimpleDocTemplate(buffer, pagesize=letter)
            elements = []
            styles = getSampleStyleSheet()
            
            # Custom Styles
            title_style = ParagraphStyle(
                'TitleStyle',
                parent=styles['Heading1'],
                textColor=colors.HexColor('#1f4e78'),
                spaceAfter=12
            )
            
            elements.append(Paragraph("Afrirent Depot Activity Report", title_style))
            elements.append(Paragraph(f"<b>Generated On:</b> {report_date}", styles['Normal']))
            elements.append(Spacer(1, 10))
            elements.append(Paragraph(f"<b>Total Unique Records:</b> {total_records}", styles['Normal']))
            elements.append(Paragraph(f"<b>Total Inventory Volume:</b> {total_items}", styles['Normal']))
            elements.append(Spacer(1, 15))
            
            # Table Data Layout
            if not df.empty:
                table_data = [list(df.columns)] + df.astype(str).values.tolist()
                t = Table(table_data)
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1f4e78')),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0,0), (-1,0), 10),
                    ('BOTTOMPADDING', (0,0), (-1,0), 6),
                    ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f2f2f2')),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                    ('FONTSIZE', (0,1), (-1,-1), 8),
                ]))
                elements.append(t)
                
            doc.build(elements)
            buffer.seek(0)
            return buffer.getvalue()

        pdf_data = generate_pdf()

        # Download PDF Button
        st.download_button(
            label="📥 Download Branded PDF Summary Report",
            data=pdf_data,
            file_name=f"Afrirent_Depot_Report_{report_date}.pdf",
            mime="application/pdf",
        )

        st.markdown("---")
        st.subheader("📧 Email Report to Management")
        
        with st.form("email_form"):
            sender_email = st.text_input("Sender Email Address (e.g., your email)")
            app_password = st.text_input("Email App Password (or SMTP Password)", type="password")
            recipient_email = st.text_input("Management Email Address")
            
            send_btn = st.form_submit_button("Send Report via Email")
            
            if send_btn:
                if sender_email and app_password and recipient_email:
                    try:
                        msg = MIMEMultipart()
                        msg['From'] = sender_email
                        msg['To'] = recipient_email
                        msg['Subject'] = f"Afrirent Depot Summary Report - {report_date}"
                        
                        body = f"Dear Management,\n\nPlease find attached the official Afrirent Depot summary report for {report_date}.\n\nTotal Records: {total_records}\nTotal Item Volume: {total_items}\n\nKind regards,\nAdmin Controller"
                        msg.attach(MIMEText(body, 'plain'))
                        
                        # Attach the generated PDF
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(pdf_data)
                        encoders.encode_base64(part)
                        part.add_header('Content-Disposition', f'attachment; filename=Afrirent_Depot_Report_{report_date}.pdf')
                        msg.attach(part)
                        
                        # Send via standard TLS SMTP (Gmail example port 587)
                        server = smtplib.SMTP('smtp.gmail.com', 587)
                        server.starttls()
                        server.login(sender_email, app_password)
                        server.sendmail(sender_email, recipient_email, msg.as_string())
                        server.quit()
                        
                        st.success("Report successfully emailed to management!")
                    except Exception as e:
                        st.error(f"Failed to send email. Error: {e}")
                else:
                    st.warning("Please fill in all email credentials before sending.")
    else:
        st.info("No data available to generate reports yet.")