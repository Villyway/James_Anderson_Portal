from django.core.management.base import BaseCommand
import pyodbc

class Command(BaseCommand):
    help = 'Fetch region data from external SQL Server'

    def handle(self, *args, **kwargs):
        try:
            conn = pyodbc.connect(
                "DRIVER={ODBC Driver 17 for SQL Server};"
                "SERVER=72.190.10.217;"
                "DATABASE=seminar;"
                "UID=seminar;"
                "PWD=seminar"
            )
            cursor = conn.cursor()
            cursor.execute("EXEC SpGetRegionData")
            rows = cursor.fetchall()

            for row in rows:
                print(row)

        except Exception as e:
            print("Error:", e)
