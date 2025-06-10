from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from .models import Region, County
from django.contrib import messages
from datetime import datetime
from django.core.paginator import Paginator
import csv
from django.shortcuts import render, redirect
import json

from django.http import JsonResponse, HttpResponse
from dateutil.relativedelta import relativedelta
from concurrent.futures import ThreadPoolExecutor
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

from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import connections
from datetime import datetime
from dateutil.relativedelta import relativedelta
import csv
import json
from concurrent.futures import ThreadPoolExecutor
import time

@login_required(login_url='/login/')
def dashboard_view(request):
    all_regions = []
    counties = []

    # Fetch region data using SpGetRegionData
    try:
        with connections['backup'].cursor() as cursor:
            cursor.execute("EXEC SpGetRegionData")
            region_rows = cursor.fetchall()
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
                    'id': row[0],
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

    # Get the selected region for filtering
    selected_region_name = request.GET.get('region', '')
    selected_region_id = None
    if selected_region_name:
        for region in all_regions:
            if region['name'] == selected_region_name:
                selected_region_id = region['id']
                break
        if selected_region_id is None:
            messages.error(request, f"Region '{selected_region_name}' not found.")

    # Default to the first county if none is selected
    selected_county = counties[0]['name'] if counties else ''

    # Prepare data for the graph (past 2 years)
    end_date = datetime(2025, 6, 1)
    start_date = end_date - relativedelta(years=2)
    graph_months = []
    current_date = start_date
    while current_date < end_date:
        graph_months.append(current_date.strftime('%b %Y'))
        current_date += relativedelta(months=1)

    # Fetch monthly counts for graph (parallel execution)
    monthly_counts = []
    county_graph_data = {}
    
    def fetch_monthly_counts():
        try:
            with connections['backup'].cursor() as cursor:
                cursor.execute("EXEC usp_GenerateMonthlyFilingCounts")
                rows = cursor.fetchall()
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            messages.error(request, f"Error fetching monthly counts: {str(e)}")
            return []

    # Use ThreadPoolExecutor for parallel processing
    with ThreadPoolExecutor(max_workers=2) as executor:
        monthly_future = executor.submit(fetch_monthly_counts)
        monthly_counts = monthly_future.result()

    # Process graph data
    column_to_month = {}
    if monthly_counts:
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

        # Process county graph data
        for county in counties:
            county_name = county['name']
            county_row = next(
                (row for row in monthly_counts if row.get('Source', '').lower() == county_name.lower()),
                None
            )
            if not county_row:
                counts = [0] * len(graph_months)
                running_avg = [0] * len(graph_months)
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

    # Search functionality - OPTIMIZED
    start_month_year = request.GET.get('start_month_year')
    end_month_year = request.GET.get('end_month_year')
    months_table = []
    monthly_data = []
    search_applied = False
    csv_data_aggregated = []
    
    # Store search parameters for CSV generation instead of full data
    search_params = {
        'region_id': selected_region_id,
        'start_month_year': start_month_year,
        'end_month_year': end_month_year
    }
    request.session['search_params'] = search_params

    if selected_region_id and start_month_year and end_month_year:
        try:
            start_year, start_month = map(int, start_month_year.split('-'))
            end_year, end_month = map(int, end_month_year.split('-'))
            
            start_date = datetime(start_year, start_month, 1)
            if end_month == 12:
                end_date = datetime(end_year + 1, 1, 1) - relativedelta(days=1)
            else:
                end_date = datetime(end_year, end_month + 1, 1) - relativedelta(days=1)

            if start_date <= end_date:
                search_applied = True
                
                # Optimized query execution with timeout
                start_time = time.time()
                
                with connections['backup'].cursor() as cursor:
                    # Set query timeout
                    cursor.execute("SET LOCK_TIMEOUT 30000")  # 30 seconds
                    
                    start_date_str = start_date.strftime('%m/%d/%Y')
                    end_date_str = end_date.strftime('%m/%d/%Y')
                    
                    # Only fetch aggregated data for display - much faster
                    query = f"EXEC SpGetCSVData @Region = {selected_region_id}, @StartDate = '{start_date_str}', @EndDate = '{end_date_str}'"
                    cursor.execute(query)
                    
                    # Fetch aggregated data
                    if cursor.description:
                        rows = cursor.fetchall()
                        columns = [column[0] for column in cursor.description]
                        csv_data_aggregated = [dict(zip(columns, row)) for row in rows]
                    
                    # Skip the second result set for now - we'll fetch it only when CSV is needed
                    try:
                        cursor.nextset()
                    except:
                        pass
                
                execution_time = time.time() - start_time
                messages.info(request, f"Query executed in {execution_time:.2f} seconds")

                # Generate months for the table
                current_date = start_date
                while current_date <= end_date:
                    months_table.append(current_date.strftime('%b %Y'))
                    current_date += relativedelta(months=1)

                # Process aggregated data for the monthly table
                types = ['ASN', 'Fran', 'Sales']
                type_counts = {type_val: {month: 0 for month in months_table} for type_val in types}

                if csv_data_aggregated:
                    for row in csv_data_aggregated:
                        row_type = row.get('Type', '')
                        for month_col in row:
                            if month_col == 'Type':
                                continue
                            try:
                                month_date = datetime.strptime(month_col, '%Y-%m')
                                month_key = month_date.strftime('%b %Y')
                                if month_key in months_table and row_type in types:
                                    type_counts[row_type][month_key] = row[month_col] or 0
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
    """
    Ultra-fast CSV generation using streaming and optimized database query
    """
    search_params = request.session.get('search_params', {})
    
    if not all([search_params.get('region_id'), search_params.get('start_month_year'), search_params.get('end_month_year')]):
        return HttpResponse("No search parameters available. Please perform a search first.", status=400)

    try:
        # Parse search parameters
        region_id = search_params['region_id']
        start_month_year = search_params['start_month_year']
        end_month_year = search_params['end_month_year']
        
        start_year, start_month = map(int, start_month_year.split('-'))
        end_year, end_month = map(int, end_month_year.split('-'))
        
        start_date = datetime(start_year, start_month, 1)
        if end_month == 12:
            end_date = datetime(end_year + 1, 1, 1) - relativedelta(days=1)
        else:
            end_date = datetime(end_year, end_month + 1, 1) - relativedelta(days=1)

        # Create streaming response for faster CSV generation
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="filtered_data_{start_month_year}_to_{end_month_year}.csv"'
        
        # Create CSV writer
        writer = csv.writer(response)
        
        # Write header
        fieldnames = ['Date', 'Name', 'Business', 'Address', 'City', 'State', 'Zip', 'Type', 'Source']
        writer.writerow(fieldnames)
        
        # Optimized database connection with streaming
        start_time = time.time()
        row_count = 0
        
        with connections['backup'].cursor() as cursor:
            # Optimize connection for large result sets
            cursor.execute("SET NOCOUNT ON")
            cursor.execute("SET LOCK_TIMEOUT 30000")
            cursor.execute("SET QUERY_GOVERNOR_COST_LIMIT 0")  # Remove query cost limit
            
            start_date_str = start_date.strftime('%m/%d/%Y')
            end_date_str = end_date.strftime('%m/%d/%Y')
            
            # Execute the stored procedure
            query = f"EXEC SpGetCSVData @Region = {region_id}, @StartDate = '{start_date_str}', @EndDate = '{end_date_str}'"
            cursor.execute(query)
            
            # Skip first result set (aggregated) - just move to next
            if cursor.description:
                cursor.fetchall()
            
            # Process second result set (transactional) with streaming
            if cursor.nextset() and cursor.description:
                columns = [col[0] for col in cursor.description]
                
                # Create column mapping based on actual database columns
                column_indices = {}
                for i, col in enumerate(columns):
                    if col in ['Date', 'FilingDate']:
                        column_indices['Date'] = i
                    elif col in ['Name']:
                        column_indices['Name'] = i
                    elif col in ['Business']:
                        column_indices['Business'] = i
                    elif col in ['Address']:
                        column_indices['Address'] = i
                    elif col in ['City']:
                        column_indices['City'] = i
                    elif col in ['State']:
                        column_indices['State'] = i
                    elif col in ['Zip']:
                        column_indices['Zip'] = i
                    elif col in ['Type', 'RecordType']:
                        column_indices['Type'] = i
                    elif col in ['Source']:
                        column_indices['Source'] = i
                
                # Stream data row by row instead of loading all into memory
                batch_size = 1000
                batch = []
                
                while True:
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    
                    for row in rows:
                        csv_row = []
                        for field in fieldnames:
                            if field in column_indices:
                                value = row[column_indices[field]]
                                
                                # Handle different data types and formatting
                                if value is None:
                                    csv_row.append('')
                                elif field == 'Date' and value:
                                    # Handle date formatting
                                    try:
                                        if isinstance(value, datetime):
                                            csv_row.append(value.strftime('%Y-%m-%d'))
                                        elif isinstance(value, str):
                                            # Try to parse string date
                                            try:
                                                parsed_date = datetime.strptime(value, '%Y-%m-%d')
                                                csv_row.append(parsed_date.strftime('%Y-%m-%d'))
                                            except:
                                                csv_row.append(str(value))
                                        else:
                                            csv_row.append(str(value))
                                    except:
                                        csv_row.append(str(value) if value else '')
                                elif field in ['Zip']:
                                    # Handle ZIP codes as strings to preserve leading zeros
                                    csv_row.append(str(value) if value else '')
                                else:
                                    # Convert all other values to string
                                    csv_row.append(str(value) if value is not None else '')
                            else:
                                csv_row.append('')  # Field not found in database
                        
                        batch.append(csv_row)
                        row_count += 1
                    
                    # Write batch to response
                    writer.writerows(batch)
                    batch = []
                    
                    # Optional: Add progress indicator for very large datasets
                    if row_count % 10000 == 0:
                        response.write(f'# Progress: {row_count} rows processed\n'.encode('utf-8'))
                
                execution_time = time.time() - start_time
                
                # Add summary comment at the end
                response.write(f'# CSV generation completed in {execution_time:.2f} seconds\n'.encode('utf-8'))
                response.write(f'# Total rows: {row_count}\n'.encode('utf-8'))
                
            else:
                return HttpResponse("No transactional data available for the selected criteria.", status=400)
        
        return response
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        return HttpResponse(f"Error generating CSV: {str(e)}\n\nDetails:\n{error_detail}", status=500)


# Alternative: Super fast CSV using raw SQL (if stored procedure is slow)
def generate_csv_raw_sql(request):
    """
    Alternative CSV generation using direct SQL instead of stored procedure
    This might be faster for large datasets
    """
    search_params = request.session.get('search_params', {})
    
    if not all([search_params.get('region_id'), search_params.get('start_month_year'), search_params.get('end_month_year')]):
        return HttpResponse("No search parameters available. Please perform a search first.", status=400)

    try:
        region_id = search_params['region_id']
        start_month_year = search_params['start_month_year']
        end_month_year = search_params['end_month_year']
        
        start_year, start_month = map(int, start_month_year.split('-'))
        end_year, end_month = map(int, end_month_year.split('-'))
        
        start_date = datetime(start_year, start_month, 1)
        if end_month == 12:
            end_date = datetime(end_year + 1, 1, 1) - relativedelta(days=1)
        else:
            end_date = datetime(end_year, end_month + 1, 1) - relativedelta(days=1)

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="filtered_data_{start_month_year}_to_{end_month_year}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Date', 'Name', 'Business', 'Address', 'City', 'State', 'Zip', 'Type', 'Source'])
        
        with connections['backup'].cursor() as cursor:
            # Direct SQL query - adjust table and column names based on your schema
            sql_query = """
            SELECT 
                CONVERT(VARCHAR(10), FilingDate, 120) as Date,
                ISNULL(Name, '') as Name,
                ISNULL(Business, '') as Business,
                ISNULL(Address, '') as Address,
                ISNULL(City, '') as City,
                ISNULL(State, '') as State,
                ISNULL(Zip, '') as Zip,
                ISNULL(RecordType, '') as Type,
                ISNULL(Source, '') as Source
            FROM YourTableName 
            WHERE Region = %s 
            AND FilingDate >= %s 
            AND FilingDate <= %s
            ORDER BY FilingDate DESC
            """
            
            cursor.execute(sql_query, [region_id, start_date, end_date])
            
            # Stream results
            while True:
                rows = cursor.fetchmany(1000)
                if not rows:
                    break
                writer.writerows(rows)
        
        return response
        
    except Exception as e:
        return HttpResponse(f"Error generating CSV with raw SQL: {str(e)}", status=500)


# Debug function to check stored procedure columns
def debug_stored_procedure_columns(request):
    """
    Debug function to see what columns your stored procedure actually returns
    Call this to understand the column structure
    """
    if not request.user.is_staff:  # Only for admin users
        return HttpResponse("Access denied", status=403)
    
    try:
        with connections['backup'].cursor() as cursor:
            # Test with sample parameters
            cursor.execute("EXEC SpGetCSVData @Region = 1, @StartDate = '01/01/2025', @EndDate = '02/28/2025'")
            
            # First result set
            columns1 = [col[0] for col in cursor.description] if cursor.description else []
            sample_row1 = cursor.fetchone() if columns1 else None
            
            # Second result set
            columns2 = []
            sample_row2 = None
            if cursor.nextset() and cursor.description:
                columns2 = [col[0] for col in cursor.description]
                sample_row2 = cursor.fetchone()
            
            debug_info = {
                'first_result_set': {
                    'columns': columns1,
                    'sample_row': sample_row1
                },
                'second_result_set': {
                    'columns': columns2,
                    'sample_row': sample_row2
                }
            }
            
            return HttpResponse(f"Debug Info:\n{json.dumps(debug_info, indent=2, default=str)}", content_type='text/plain')
            
    except Exception as e:
        return HttpResponse(f"Debug Error: {str(e)}", content_type='text/plain')


# Additional helper function for async processing (optional)
def get_csv_data_async(region_id, start_date_str, end_date_str):
    """
    Async function to fetch CSV data - can be used with Celery for background processing
    """
    try:
        with connections['backup'].cursor() as cursor:
            query = f"EXEC SpGetCSVData @Region = {region_id}, @StartDate = '{start_date_str}', @EndDate = '{end_date_str}'"
            cursor.execute(query)
            
            # Skip aggregated data
            if cursor.description:
                cursor.fetchall()
            
            # Get transactional data
            if cursor.nextset() and cursor.description:
                rows = cursor.fetchall()
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in rows]
            
        return []
    except Exception as e:
        raise Exception(f"Database error: {str(e)}")


# Performance monitoring decorator
def monitor_performance(func):
    """
    Decorator to monitor function performance
    """
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        execution_time = time.time() - start_time
        
        # Log performance (you can customize this)
        print(f"{func.__name__} executed in {execution_time:.2f} seconds")
        
        return result
    return wrapper

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
def manage_do_not_mail_1(request):
    records = []
    search_query = request.GET.get('search_query', '') if request.method == 'GET' else request.POST.get('search_query', '')
    
    # Handle marking records as "Do Not Mail" (POST request)
    if request.method == 'POST':
        selected_mark_ids = []
        selected_records = []
        
        for key, value in request.POST.items():
            if key.startswith('record_') and value == 'on':
                record_id = key.split('_')[1]
                selected_mark_ids.append(record_id)

        if not selected_mark_ids:
            messages.error(request, "Please select at least one record to mark as Do Not Mail.")
        else:
            try:
                # Get record details before marking
                with connections['backup'].cursor() as cursor:
                    if search_query and search_query.strip():
                        cursor.execute("EXEC GetSearchResult @searchtext = %s", [search_query.strip()])
                    
                    if cursor.description:
                        rows = cursor.fetchall()
                        columns = [column[0] for column in cursor.description]
                        all_records = [dict(zip(columns, row)) for row in rows]
                        
                        # Find selected records details
                        for record in all_records:
                            if str(record.get('ID')) in selected_mark_ids:
                                selected_records.append(record)
                
                # Mark records as Do Not Mail
                with connections['backup'].cursor() as cursor:
                    ids_param = ','.join(selected_mark_ids)
                    cursor.execute("EXEC MarkDontMail @ids = %s", [ids_param])
                    
                    # Create success message with record details
                    success_msg = f"Successfully marked {len(selected_mark_ids)} record(s) as Do Not Mail:"
                    for record in selected_records:
                        name = record.get('Name', 'N/A')
                        address = record.get('Address', 'N/A')
                        success_msg += f" • {name} - {address}"
                    
                    messages.success(request, success_msg)
                    
            except Exception as e:
                messages.error(request, f"Error marking records: {str(e)}")

    # Get data based on search query (for both GET and POST after processing)
    if search_query and search_query.strip():
        try:
            with connections['backup'].cursor() as cursor:
                # Use GetSearchResult stored procedure for search
                cursor.execute("EXEC GetSearchResult @searchtext = %s", [search_query.strip()])
                
                if cursor.description:
                    rows = cursor.fetchall()
                    columns = [column[0] for column in cursor.description]
                    records = [dict(zip(columns, row)) for row in rows]
                    
                    if not records and request.method == 'GET':
                        messages.warning(request, f'No records found matching "{search_query}". Please try different search terms.')
                    elif records and request.method == 'GET':
                        messages.success(request, f'Found {len(records)} records matching "{search_query}"')
                        
        except Exception as e:
            messages.error(request, f"Error fetching data: {str(e)}")

    # Implement pagination for results
    paginator = Paginator(records, 50)  # Show 50 records per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'records': page_obj,
        'search_query': search_query,
        'is_paginated': paginator.num_pages > 1,
        'page_obj': page_obj,
    }
    return render(request, 'dashboard/manage_do_not_mail_1.html', context)