from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from .models import Region, County
from django.contrib import messages
from datetime import datetime
import csv
from django.http import JsonResponse
import pyodbc
from django.http import HttpResponse
from dateutil.relativedelta import relativedelta
from django.db import connections

def get_region_data(request):
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
        columns = [column[0] for column in cursor.description]
        data = [dict(zip(columns, row)) for row in rows]
        return JsonResponse({'data': data})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None and user.is_superuser:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, 'Invalid credentials or not a superadmin.')
    return render(request, 'dashboard/login.html')

@login_required
def dashboard_view(request):
    all_regions = []
    counties = []

    # Fetch region data using SpGetRegionData
    try:
        with connections['backup'].cursor() as cursor:
            cursor.execute("EXEC SpGetRegionData")
            region_rows = cursor.fetchall()
            Region.objects.all().delete()
            for row in region_rows:
                region = Region(
                    name=row[1],
                    total_rows=row[2],
                    start_date=row[3],
                    end_date=row[4],
                )
                region.save()
                region_data = {
                    'id': region.id,
                    'name': row[1],
                    'total_rows': row[2],
                    'start_date': row[3],
                    'end_date': row[4],
                }
                all_regions.append(region_data)
    except Exception as e:
        messages.error(request, f"Error fetching region data: {str(e)}")

    # Fetch county data using SpGetCountyData
    try:
        with connections['backup'].cursor() as cursor:
            cursor.execute("EXEC SpGetCountyData")
            county_rows = cursor.fetchall()
            County.objects.all().delete()
            for row in county_rows:
                county = County(
                    name=row[1],
                    total_rows=row[2],
                    start_date=row[3],
                    end_date=row[4],
                )
                county.save()
                county_data = {
                    'id': county.id,
                    'name': row[1],
                    'total_rows': row[2],
                    'start_date': row[3],
                    'end_date': row[4],
                }
                counties.append(county_data)
    except Exception as e:
        messages.error(request, f"Error fetching county data: {str(e)}")

    # Debug: Log the counties list
    messages.info(request, f"Counties from SpGetCountyData: {[county['name'] for county in counties]}")

    # Get the selected region and county for filtering (search form)
    selected_region = request.GET.get('region', '')
    selected_county = request.GET.get('county', '')
    
    # Default to the first county if none is selected (for search form)
    if not selected_county and counties:
        selected_county = counties[0]['name']

    # Fetch monthly counts using usp_GenerateMonthlyFilingCounts
    monthly_counts = []
    try:
        with connections['backup'].cursor() as cursor:
            cursor.execute("EXEC usp_GenerateMonthlyFilingCounts")
            rows = cursor.fetchall()
            columns = [column[0] for column in cursor.description]
            monthly_counts = [dict(zip(columns, row)) for row in rows]

            # Debug: Log the Source values
            sources = [row.get('Source', '') for row in monthly_counts]
            messages.info(request, f"Sources from usp_GenerateMonthlyFilingCounts: {sources}")

            # Filter by county if selected (for search form, not graph)
            if selected_county:
                monthly_counts = [
                    row for row in monthly_counts
                    if row.get('Source', '').lower() == selected_county.lower()
                ]
    except Exception as e:
        messages.error(request, f"Error fetching monthly counts: {str(e)}")
        monthly_counts = []

    # Prepare data for the graph (past 2 years: June 2023 to May 2025)
    end_date = datetime(2025, 6, 1)  # June 2025
    start_date = end_date - relativedelta(years=2)  # June 2023
    graph_months = []
    current_date = start_date
    while current_date < end_date:
        graph_months.append(current_date.strftime('%b %Y'))  # e.g., 'Jun 2023'
        current_date += relativedelta(months=1)

    # Map stored procedure columns to graph months (e.g., 'Jul 23' -> 'Jul 2023')
    column_to_month = {}
    for col in columns:
        if col == 'Source':
            continue
        try:
            month_date = datetime.strptime(col, '%b %y')
            month_label = month_date.strftime('%b %Y')
            column_to_month[col] = month_label
        except ValueError:
            continue

    # Aggregate counts by county and month
    county_graph_data = {}
    monthly_counts_all = [dict(zip(columns, row)) for row in rows]  # Keep an unfiltered copy for the graph
    if monthly_counts_all:
        for county in counties:
            county_name = county['name']
            # Find the row for this county (case-insensitive match)
            county_row = next(
                (row for row in monthly_counts_all if row.get('Source', '').lower() == county_name.lower()),
                None
            )
            if not county_row:
                messages.info(request, f"No data found for county: {county_name}")
                counts = [0] * len(graph_months)
            else:
                counts = []
                for month in graph_months:
                    col_name = next((col for col, m in column_to_month.items() if m == month), None)
                    count = county_row.get(col_name, 0) if col_name else 0
                    counts.append(count)

                # Calculate 3-month running average
                running_avg = []
                for i in range(len(counts)):
                    if i < 2:
                        avg = sum(counts[:i+1]) / (i+1)
                    else:
                        avg = sum(counts[i-2:i+1]) / 3
                    running_avg.append(round(avg, 2))
                
                county_graph_data[county_name] = {
                    'counts': counts,
                    'running_avg': running_avg
                }
    else:
        for county in counties:
            county_name = county['name']
            county_graph_data[county_name] = {
                'counts': [0] * len(graph_months),
                'running_avg': [0] * len(graph_months)
            }

    # Debug: Log the county_graph_data keys
    messages.info(request, f"County Graph Data Keys: {list(county_graph_data.keys())}")

    # Search functionality for the monthly data table
    start_month_year = request.GET.get('start_month_year')
    end_month_year = request.GET.get('end_month_year')
    months_table = []
    monthly_data = []
    search_applied = False
    if start_month_year and end_month_year:
        try:
            start_year, start_month = start_month_year.split('-')
            end_year, end_month = end_month_year.split('-')
            start_date_table = datetime(int(start_year), int(start_month), 1)
            end_date_table = datetime(int(end_year), int(end_month), 1)
            
            if start_date_table <= end_date_table:
                search_applied = True
                current_date = start_date_table
                while current_date <= end_date_table:
                    months_table.append(current_date.strftime('%b %Y'))
                    current_date += relativedelta(months=1)
                
                types = ['ASN', 'Fran', 'Sales']
                for type_value in types:
                    data_row = {
                        'type': type_value,
                        'months': {},
                        'total': 0
                    }
                    for month in months_table:
                        data_row['months'][month] = 0
                        data_row['total'] += 0
                    monthly_data.append(data_row)
        except ValueError:
            messages.error(request, 'Invalid date values provided.')

    # Pass data to the template
    context = {
        'regions': all_regions,
        'counties': counties,
        'all_regions': all_regions,
        'selected_region': selected_region,
        'selected_county': selected_county,
        'search_applied': search_applied,
        'months': months_table,
        'monthly_data': monthly_data,
        'graph_months': graph_months,
        'county_graph_data': county_graph_data,
    }
    return render(request, 'dashboard/dashboard.html', context)

@login_required
def generate_csv(request):
    # Get the filter parameters from the request
    selected_region = request.GET.get('region', '')
    start_month_year = request.GET.get('start_month_year')
    end_month_year = request.GET.get('end_month_year')

    # Original CSV generation logic for Django models
    counties = County.objects.all()
    filtered_data = []

    # Filter counties based on search criteria
    if start_month_year and end_month_year:
        try:
            # Parse YYYY-MM format
            start_year, start_month = start_month_year.split('-')
            end_year, end_month = end_month_year.split('-')
            
            start_date = datetime(int(start_year), int(start_month), 1)
            end_date = datetime(int(end_year), int(end_month), 1)
            
            # Filter counties by date range
            filtered_counties = counties.filter(start_date__gte=start_date, end_date__lte=end_date)
            
            # If a specific region is selected, filter by region
            if selected_region:
                # No region field in County model, so we'll skip this for now
                pass

            # Generate the data for CSV
            for county in filtered_counties:
                row_data = {
                    'date': county.start_date.strftime('%Y-%m-%d') if county.start_date else '',
                    'name': county.name,
                    'business': '',  # Not available in County model
                    'address': '',  # Not available in County model
                    'city': '',  # Not available in County model
                    'state': '',  # Not available in County model
                    'zip': '',  # Not available in County model
                    'type': '',  # Type field removed
                    'source': '',  # Not available in County model
                }
                filtered_data.append(row_data)
        
        except ValueError:
            pass  # Return empty CSV if invalid dates
    else:
        # If no date filter, export all counties
        for county in counties:
            row_data = {
                'date': county.start_date.strftime('%Y-%m-%d') if county.start_date else '',
                'name': county.name,
                'business': '',
                'address': '',
                'city': '',
                'state': '',
                'zip': '',
                'type': '',
                'source': '',
            }
            filtered_data.append(row_data)

    # Create the CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="filtered_data.csv"'

    writer = csv.writer(response)
    
    # Write headers as specified
    headers = ['Date', 'Name', 'Business', 'Address', 'City', 'State', 'Zip', 'Type', 'Source']
    writer.writerow(headers)

    # Write data rows
    for data in filtered_data:
        row = [
            data['date'],
            data['name'],
            data['business'],
            data['address'],
            data['city'],
            data['state'],
            data['zip'],
            data['type'],
            data['source']
        ]
        writer.writerow(row)

    return response

@login_required
def logout_view(request):
    logout(request)
    return redirect('login')

@login_required
def update_county(request, county_id):
    county = get_object_or_404(County, id=county_id)
    if request.method == 'POST':
        county.start_date = request.POST.get('start_date')
        county.end_date = request.POST.get('end_date')
        county.save()
        messages.success(request, 'County updated successfully.')
        return redirect('dashboard')
    return render(request, 'dashboard/update_county.html', {'county': county})

@login_required
def manage_do_not_mail(request):
    counties = County.objects.all()
    search_query = request.GET.get('search', '')
    if search_query:
        counties = counties.filter(name__icontains=search_query)

    context = {
        'counties': counties,
        'search_query': search_query,
    }
    return render(request, 'dashboard/manage_do_not_mail.html', context)