import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


def send_email(recipient_email, subject, body):
    try:
        message = Mail(
            from_email='abelrevelation@gmail.com',
            to_emails=recipient_email,
            subject=subject,
            html_content=body.replace('\n', '<br>')
        )

        api_key = os.environ.get('SENDGRID_API_KEY')
        if not api_key:
            
            return

        sg = SendGridAPIClient(api_key)
        sg.send(message)

    except Exception as e:
        print(f"Email failed: {e}")