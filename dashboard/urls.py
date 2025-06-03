from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('logout/', views.logout_view, name='logout'),
    path('update_county/<int:county_id>/', views.update_county, name='update_county'),
    path('manage-do-not-mail/', views.manage_do_not_mail, name='manage_do_not_mail'),
    path('generate-csv/', views.generate_csv, name='generate_csv'),
    
    path('api/get-region/', views.get_region_data),
]