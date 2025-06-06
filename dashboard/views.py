from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from .models import Region, County
from django.contrib import messages
from datetime import datetime
import csv
from django.http import JsonResponse, HttpResponse
from dateutil.relativedelta import relativedelta
from django.db import connections

def get_region_data(request):
    try:
        with connections['backup'].cursor() as cursor:
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
            # Log columns only if there's an issue
            if not region_rows:
                columns = [column[0] for column in cursor.description]
                messages.info(request, f"SpGetRegionData columns: {columns} (No data returned)")
            
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
                    'id': row[0],  # Region ID
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
                    'id': row[0],
                    'name': row[1],
                    'total_rows': row[2],
                    'start_date': row[3],
                    'end_date': row[4],
                }
                counties.append(county_data)
    except Exception as e:
        messages.error(request, f"Error fetching county data: {str(e)}")

    # Get the selected region for filtering (search form)
    selected_region_name = request.GET.get('region', '')
    selected_region_id = None
    if selected_region_name:
        # Find the region ID corresponding to the region name
        for region in all_regions:
            if region['name'] == selected_region_name:
                selected_region_id = region['id']
                break
        if selected_region_id is None:
            messages.error(request, f"Region '{selected_region_name}' not found.")

    # Default to the first county if none is selected (for graph)
    selected_county = counties[0]['name'] if counties else ''

    # Prepare data for the graph (past 2 years: June 2023 to May 2025)
    end_date = datetime(2025, 6, 1)  # June 2025
    start_date = end_date - relativedelta(years=2)  # June 2023
    graph_months = []
    current_date = start_date
    while current_date < end_date:
        graph_months.append(current_date.strftime('%b %Y'))  # e.g., 'Jun 2023'
        current_date += relativedelta(months=1)

    # Fetch monthly counts using usp_GenerateMonthlyFilingCounts for the graph
    monthly_counts = []
    try:
        with connections['backup'].cursor() as cursor:
            cursor.execute("EXEC usp_GenerateMonthlyFilingCounts")
            rows = cursor.fetchall()
            columns = [column[0] for column in cursor.description]
            monthly_counts = [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        messages.error(request, f"Error fetching monthly counts: {str(e)}")
        monthly_counts = []  # Set to empty list to prevent further errors

    # Initialize county_graph_data as an empty dictionary
    county_graph_data = {}

    # Map stored procedure columns to graph months (e.g., 'Jul 23' -> 'Jul 2023')
    column_to_month = {}
    if monthly_counts:  # Only process if data is available
        columns = list(monthly_counts[0].keys())
        for col in columns:
            if col == 'Source':
                continue
            try:
                month_date = datetime.strptime(col, '%b %y')
                month_label = month_date.strftime('%b %Y')
                column_to_month[col] = month_label
            except ValueError:
                continue

        # Aggregate counts by county and month for the graph
        monthly_counts_all = monthly_counts  # Unfiltered copy for the graph
        for county in counties:
            county_name = county['name']
            county_row = next(
                (row for row in monthly_counts_all if row.get('Source', '').lower() == county_name.lower()),
                None
            )
            if not county_row:
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
                    'running_avg': running_avg,
                }
    else:
        for county in counties:
            county_name = county['name']
            county_graph_data[county_name] = {
                'counts': [0] * len(graph_months),
                'running_avg': [0] * len(graph_months),
            }

    # Search functionality using SpGetCSVData
    start_month_year = request.GET.get('start_month_year')
    end_month_year = request.GET.get('end_month_year')
    months_table = []
    monthly_data = []
    search_applied = False
    csv_data = []  # Store data for CSV generation

    if selected_region_id and start_month_year and end_month_year:
        try:
            # Parse YYYY-MM format and set day for start and end of month
            start_year, start_month = map(int, start_month_year.split('-'))
            end_year, end_month = map(int, end_month_year.split('-'))
            
            # Set start_date to the first day of the start month
            start_date = datetime(start_year, start_month, 1)
            
            # Set end_date to the last day of the end month
            if end_month == 12:
                end_date = datetime(end_year + 1, 1, 1) - relativedelta(days=1)
            else:
                end_date = datetime(end_year, end_month + 1, 1) - relativedelta(days=1)

            if start_date <= end_date:
                search_applied = True
                # Call SpGetCSVData with parameters
                with connections['backup'].cursor() as cursor:
                    # Debug: Log the parameters
                    messages.info(request, f"Calling SpGetCSVData with Region: {selected_region_id}, StartDate: {start_date.strftime('%m/%d/%Y')}, EndDate: {end_date.strftime('%m/%d/%Y')}")
                    
                    # Format the query string directly
                    start_date_str = start_date.strftime('%m/%d/%Y')
                    end_date_str = end_date.strftime('%m/%d/%Y')
                    query = f"EXEC SpGetCSVData @Region = {selected_region_id}, @StartDate = '{start_date_str}', @EndDate = '{end_date_str}'"
                    cursor.execute(query)
                    
                    # Fetch the results
                    rows = cursor.fetchall()
                    columns = [column[0] for column in cursor.description]
                    csv_data = [dict(zip(columns, row)) for row in rows]

                # Debug: Log the number of rows fetched
                messages.info(request, f"Fetched {len(csv_data)} rows from SpGetCSVData")

                # Debug: Log the raw data
                messages.info(request, f"Raw CSV Data: {csv_data}")

                # Generate months for the table
                current_date = start_date
                while current_date <= end_date:
                    months_table.append(current_date.strftime('%b %Y'))
                    current_date += relativedelta(months=1)

                # Aggregate data for the monthly table (data is already aggregated)
                types = ['ASN', 'Fran', 'Sales']
                type_counts = {type_val: {month: 0 for month in months_table} for type_val in types}

                for row in csv_data:
                    row_type = row.get('Type', '')
                    # Map the counts from the columns (e.g., '2025-01') to the table format (e.g., 'Jan 2025')
                    for month_col in row:
                        if month_col == 'Type':
                            continue
                        try:
                            # Convert '2025-01' to 'Jan 2025'
                            month_date = datetime.strptime(month_col, '%Y-%m')
                            month_key = month_date.strftime('%b %Y')
                            if month_key in months_table and row_type in types:
                                type_counts[row_type][month_key] = row[month_col]
                        except ValueError:
                            continue

                # Format data for the template
                for type_val in types:
                    data_row = {
                        'type': type_val,
                        'months': type_counts[type_val],
                    }
                    monthly_data.append(data_row)

        except Exception as e:
            messages.error(request, f"Error fetching CSV data: {str(e)}")

    # Store csv_data in session to use in generate_csv view
    request.session['csv_data'] = csv_data

    # Pass data to the template
    context = {
        'regions': all_regions,
        'counties': counties,
        'all_regions': all_regions,
        'selected_region': selected_region_name,
        'selected_county': selected_county,
        'search_applied': search_applied,
        'months': months_table,
        'monthly_data': monthly_data,
        'graph_months': graph_months,
        'county_graph_data': county_graph_data,
    }
    return render(request, 'dashboard/dashboard.html', context)

def generate_csv(request):
    csv_data = request.session.get('csv_data', [])
    
    if not csv_data:
        return HttpResponse("No data available to generate CSV.", status=400)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="filtered_data.csv"'

    # Define fieldnames based on the aggregated data
    if csv_data:
        fieldnames = ['Type'] + [col for col in csv_data[0].keys() if col != 'Type']
    else:
        fieldnames = ['Type']

    writer = csv.DictWriter(response, fieldnames=fieldnames)
    writer.writeheader()
    
    for row in csv_data:
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