# Generated by Django 2.2.24 on 2021-11-23 10:40

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0010_auto_20210402_1439'),
    ]

    operations = [
        migrations.AlterField(
            model_name='assetgroup',
            name='criticity',
            field=models.CharField(choices=[('low', 'low'), ('medium', 'medium'), ('high', 'high')], default='low', max_length=10),
        ),
    ]
