from django.db import models

class Region(models.Model):
    name = models.CharField(max_length=100, db_index=True)
    total_rows = models.IntegerField()
    start_date = models.DateField()
    end_date = models.DateField()

    def __str__(self):
        return self.name

class County(models.Model):
    name = models.CharField(max_length=100, db_index=True)
    total_rows = models.IntegerField()
    start_date = models.DateField()
    end_date = models.DateField()

    def __str__(self):
        return self.name