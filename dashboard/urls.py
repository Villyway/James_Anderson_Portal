from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('logout/', views.logout_view, name='logout'),
    path('update_county/<int:county_id>/', views.update_county, name='update_county'),
    path('generate-csv/', views.generate_csv, name='generate_csv'),
    path('manage_do_not_mail_1/', views.manage_do_not_mail_1, name='manage_do_not_mail_1'),
    
    path('api/get-region/', views.get_region_data),
    
    path('debug-columns/', views.debug_stored_procedure_columns, name='debug_columns'),
]