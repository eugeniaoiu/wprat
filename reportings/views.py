from django.contrib.auth.decorators import login_required
from django.contrib.postgres.fields.jsonb import KeyTextTransform
from django.db.models import Q, Count, Case, When, Sum
from django.db.models.functions import Coalesce
from django.db import models
from django.shortcuts import render
from django.conf import settings

from assets.models import Asset, AssetGroup, ASSET_TYPES
from findings.models import Finding
from scans.models import Scan, ScanDefinition
from engines.models import EngineInstance, EnginePolicy
from rules.models import Rule
from events.models import Alert
from users.models import Team, TeamUser

import datetime
import operator
import copy
import ast


@login_required
def homepage_dashboard_view(request):
    teamid_selected = -1
    if settings.PRO_EDITION is True and int(request.GET.get('team', -1)) >= 0:
        teamid = int(request.GET.get('team'))
        # @Todo: ensure the team is allowed for this user
        teamid_selected = teamid

    # Findings
    if teamid_selected >= 0:
        findings = Finding.objects.for_team(request.user, teamid_selected).all().only("status", "severity", "vuln_refs", "risk_info", "title", "asset_name")
        assets = Asset.objects.for_team(request.user, teamid_selected).all()
        assetgroups = AssetGroup.objects.for_team(request.user, teamid_selected).all()
        scan_definitions = ScanDefinition.objects.for_team(request.user, teamid_selected).all()
        scans = Scan.objects.for_team(request.user, teamid_selected).all()
        alerts_new = Alert.objects.for_team(request.user, teamid_selected).filter(status="new")
    else:
        findings = Finding.objects.for_user(request.user).all().only("status", "severity", "vuln_refs", "risk_info", "title", "asset_name")
        assets = Asset.objects.for_user(request.user).all()
        assetgroups = AssetGroup.objects.for_user(request.user).all()
        scan_definitions = ScanDefinition.objects.for_user(request.user).all()
        scans = Scan.objects.for_user(request.user).all()
        alerts_new = Alert.objects.for_user(request.user).filter(status="new")

    alerts_new_info = Count('id', filter=Q(severity="info"))
    alerts_new_low = Count('id', filter=Q(severity="low"))
    alerts_new_medium = Count('id', filter=Q(severity="medium"))
    alerts_new_high = Count('id', filter=Q(severity="high"))
    alerts_new_critical = Count('id', filter=Q(severity="critical"))
    alerts = alerts_new.aggregate(alerts_new_info=alerts_new_info,alerts_new_low=alerts_new_low,alerts_new_medium=alerts_new_medium,alerts_new_high=alerts_new_high,alerts_new_critical=alerts_new_critical)

    oneyear_threshold = datetime.datetime.now() - datetime.timedelta(days=365)

    global_stats = {
        "assets": {
            "total": assets.count(),
            "total_ag": assetgroups.count(),
        },
        "asset_types": {},
        "findings": {},
        "scans": {
            # "defined": ScanDefinition.objects.for_user(request.user).all().count(),
            "defined": scan_definitions.count(),
            # "performed": Scan.objects.for_user(request.user).all().count(),
            "running": scans.filter(status="started").count(),
            "enqueued": scans.filter(status="enqueued").count(),
            "performed": scans.count(),
            "performed_since_1y": scans.filter(created_at__gt=oneyear_threshold).count(),
            # "active_periodic": ScanDefinition.objects.for_user(request.user).filter(enabled=True, scan_type='periodic').count(),
            "active_periodic": scan_definitions.filter(enabled=True, scan_type='periodic').count(),
        },
        "engines": {
            "total": EngineInstance.objects.all().count(),
            "policies": EnginePolicy.objects.all().count(),
            "active": EngineInstance.objects.filter(status='READY', enabled=True).count(),
        },
        "rules": {
            "total": Rule.objects.all().count(),
            "active": Rule.objects.filter(enabled=True).count(),
            "nb_matches": 0,
        },
        "alerts": {
            "total_new": alerts_new.count(),
            "new_info": alerts['alerts_new_info'],
            "new_low": alerts['alerts_new_low'],
            "new_medium": alerts['alerts_new_medium'],
            "new_high": alerts['alerts_new_high'],
            "new_critical": alerts['alerts_new_critical'],
        },
    }

    # asset types
    asset_types_stats_params = {}
    for at in ASSET_TYPES:
        asset_types_stats_params.update(
            {at[0]: Coalesce(Sum(
                Case(When(type=at[0], then=1)),
                output_field=models.IntegerField()), 0)
            }
        )
    global_stats["asset_types"] = assets.aggregate(**asset_types_stats_params)

    # Finding counters
    findings_stats = findings.exclude(Q(status='false-positive') | Q(status='duplicate')).aggregate(
        nb_new=Count('id', filter=Q(status='new')),
        nb_critical=Count('id', filter=Q(severity='critical')),
        nb_high=Count('id', filter=Q(severity='high')),
        nb_medium=Count('id', filter=Q(severity='medium')),
        nb_low=Count('id', filter=Q(severity='low')),
        nb_info=Count('id', filter=Q(severity='info')),
        cvss_lte5=Count('id', filter=Q(risk_info__cvss_base_score__lt=5)),
        cvss_5to7=Count('id', filter=Q(risk_info__cvss_base_score__lt=7, risk_info__cvss_base_score__gte=5)),
        cvss_gte7=Count('id', filter=Q(risk_info__cvss_base_score__lt=9, risk_info__cvss_base_score__gte=7)),
        cvss_gte9=Count('id', filter=Q(risk_info__cvss_base_score__lt=10, risk_info__cvss_base_score__gte=9)),
        cvss_eq10=Count('id', filter=Q(risk_info__cvss_base_score=10))
    )
    global_stats["findings"] = {
        "total": findings_stats["nb_critical"]+findings_stats["nb_high"]+findings_stats["nb_medium"]+findings_stats["nb_low"]+findings_stats["nb_info"],
        "new": findings_stats["nb_new"],
        "critical": findings_stats["nb_critical"],
        "high": findings_stats["nb_high"],
        "medium": findings_stats["nb_medium"],
        "low": findings_stats["nb_low"],
        "info": findings_stats["nb_info"],
    }

    cvss_scores = {'lte5': 0, '5to7': 0, 'gte7': 0, 'gte9': 0, 'eq10': 0}
    cvss_scores.update({
        'lte5': findings_stats["cvss_lte5"],
        '5to7': findings_stats["cvss_5to7"],
        'gte7': findings_stats["cvss_gte7"],
        'gte9': findings_stats["cvss_gte9"],
        'eq10': findings_stats["cvss_eq10"],
    })

    # update nb_matches
    matches = 0
    for r in Rule.objects.all():
        matches += r.nb_matches
    global_stats["rules"].update({"nb_matches": matches})

    # Last 6 findings
    last_findings = findings.order_by('-id')[:6][::-1]

    # Last 6 scans
    last_scans = scans.order_by('-started_at')[:6]

    # Asset grades repartition and TOP 10
    asset_grades_map = {
        "A": {"high": 0, "medium": 0, "low": 0},
        "B": {"high": 0, "medium": 0, "low": 0},
        "C": {"high": 0, "medium": 0, "low": 0},
        "D": {"high": 0, "medium": 0, "low": 0},
        "E": {"high": 0, "medium": 0, "low": 0},
        "F": {"high": 0, "medium": 0, "low": 0},
        "-": {"high": 0, "medium": 0, "low": 0}
    }

    assetgroup_grades_map = copy.deepcopy(asset_grades_map)

    # Asset grades
    assets_risk_scores = {}
    for asset in assets.only("risk_level", "criticity", "id"):
        asset_grades_map[asset.risk_level["grade"]].update({
            asset.criticity: asset_grades_map[asset.risk_level["grade"]][asset.criticity] + 1
        })
        assets_risk_scores.update({asset.id: asset.get_risk_score()})

    top_critical_assets_scores = sorted(assets_risk_scores.items(), key=operator.itemgetter(1))[::-1][:6]

    tcas_id_list = [id for id, score in top_critical_assets_scores]
    top_critical_assets = list(assets.filter(id__in=tcas_id_list))
    top_critical_assets.sort(key=lambda t: tcas_id_list.index(t.id))

    # Format to list
    asset_grades_map_list = []
    for key in sorted(asset_grades_map.keys()):
        asset_grades_map_list.append({key: asset_grades_map[key]})

    # Asset groups
    assetgroups_risk_scores = {}
    ags = assetgroups.only("risk_level", "criticity", "id", "name")

    for assetgroup in ags:
        assetgroup_grades_map[assetgroup.risk_level["grade"]].update({
            assetgroup.criticity: assetgroup_grades_map[assetgroup.risk_level["grade"]][assetgroup.criticity] + 1
        })
        assetgroups_risk_scores.update({assetgroup.id: assetgroup.get_risk_score()})

    top_critical_assetgroups_scores = sorted(assetgroups_risk_scores.items(), key=operator.itemgetter(1))[::-1][:6]
    tcags_id_list = [id for id, score in top_critical_assetgroups_scores]
    top_critical_assetgroups = list(ags.filter(id__in=tcags_id_list))
    top_critical_assetgroups.sort(key=lambda t: tcags_id_list.index(t.id))

    assetgroup_grades_map_list = []
    for key in sorted(assetgroup_grades_map.keys()):
        assetgroup_grades_map_list.append({key: assetgroup_grades_map[key]})

    # Critical findings
    active_findings = findings.exclude(status__in=['false-positive', 'duplicate', 'closed', 'patched', 'undone', 'mitigated']).only("id", "severity", "title", "asset_name")
    top_critical_findings = []
    MAX_CF = 6
    for finding in active_findings.filter(severity="critical"):
        if len(top_critical_findings) <= MAX_CF: top_critical_findings.append(finding)
    if len(top_critical_findings) <= MAX_CF:
        for finding in active_findings.filter(severity="high"):
            if len(top_critical_findings) <= MAX_CF: top_critical_findings.append(finding)
    if len(top_critical_findings) <= MAX_CF:
        for finding in active_findings.filter(severity="medium"):
            if len(top_critical_findings) <= MAX_CF: top_critical_findings.append(finding)
    if len(top_critical_findings) <= MAX_CF:
        for finding in active_findings.filter(severity="low"):
            if len(top_critical_findings) <= MAX_CF: top_critical_findings.append(finding)
    if len(top_critical_findings) <= MAX_CF:
        for finding in active_findings.filter(severity="info"):
            if len(top_critical_findings) <= MAX_CF: top_critical_findings.append(finding)

    # CVE & CWE
    cxe_stats = {}
    cve_list = {}
    cwe_list = {}

    finding_cves_list = active_findings.exclude(
            vuln_refs__CVE__isnull=True
        ).annotate(
            cvelist=KeyTextTransform("CVE", 'vuln_refs')
        ).values('cvelist')

    finding_cwes_list = active_findings.exclude(
            vuln_refs__CWE__isnull=True
        ).annotate(
            cwelist=KeyTextTransform("CWE", 'vuln_refs')
        ).values('cwelist')

    for finding_cves in finding_cves_list:
        if finding_cves['cvelist'] is not None:
            for cve in ast.literal_eval(finding_cves['cvelist']):
                if cve not in cve_list.keys():
                    cve_list.update({cve: 1})
                else:
                    cve_list.update({cve: cve_list[cve]+1})

    for cwe_data in finding_cwes_list:
        cwe = list(cwe_data.values())[0]
        if cwe not in cwe_list.keys():
            cwe_list.update({cwe: 1})
        else:
            cwe_list.update({cwe: cwe_list[cwe]+1})

    cxe_stats.update({
        'top_cve': sorted(cve_list.items(), key=lambda x: x[1], reverse=True)[:10],
        'top_cwe': sorted(cwe_list.items(), key=lambda x: x[1], reverse=True)[:10],
    })

    teams = []
    if settings.PRO_EDITION:
        if request.user.is_superuser:
            teams = Team.objects.all().order_by('name')
        else:
            for tu in TeamUser.objects.filter(user=request.user, organization__is_active=True):
                teams.append({
                    'id': tu.organization.id,
                    'name': tu.organization.name
                })

    return render(request, 'home-dashboard.html', {
        'global_stats': global_stats,
        'last_findings': last_findings,
        'last_scans': last_scans,
        'asset_grades_map': asset_grades_map_list,
        'assetgroup_grades_map': assetgroup_grades_map_list,
        'top_critical_assets': top_critical_assets,
        'top_critical_assetgroups': top_critical_assetgroups,
        'top_critical_findings': top_critical_findings,
        'cvss_scores': cvss_scores,
        'cxe_stats': cxe_stats,
        'teams': teams
    })


def patch_management_view(request):
    data = []

    # date filter
    ref_date = request.GET.get('ts', None)
    if not ref_date:
        ref_date = datetime.datetime.today()
    else:
        try:
            ref_date = datetime.datetime.strptime(ref_date, '%Y/%m/%d')
        except Exception:
            # bad date format -> today by default
            ref_date = datetime.datetime.today()

    seven_days_ago = ref_date-datetime.timedelta(days=7)
    month_ago = ref_date-datetime.timedelta(days=30)

    # asset type filter (AssetCategory)
    # asset_tags = request.GET.get('asset_tags', None)
    # if asset_tags:
    #     for tag in AssetCategory.objects.filter(value__iexact=asset_tags):
    #         print (tag)


    # Dataset 1: security fix applied 7 days max, CVSS >= 7.0
    ness_plugin_family_osupdates = [
        "aix_local_security_checks",
        "centos_local_security_checks",
        "debian_local_security_checks",
        "hp-ux_local_security_checks",
        "oracle_linux_local_security_checks",
        "red_hat_local_security_checks",
        "solaris_local_security_checks",
        "suse_local_security_checks",
        "ubuntu_local_security_checks",
        "vmware_esx_local_security_checks",
        "windows_:_microsoft_bulletins",
    ]
    dataset_7days = Asset.objects.for_user(request.user).filter(
        Q(rawfinding__created_at__gt=seven_days_ago) &
        Q(rawfinding__risk_info__vuln_publication_date__gte=seven_days_ago.strftime('%Y/%m/%d')) &
        Q(rawfinding__risk_info__cvss_base_score__gte=7.0) &
        Q(rawfinding__type__in=ness_plugin_family_osupdates)
        #Q(rawfinding__scan__engine_policy_id=1)
    #).distinct()
    ).annotate(nb_findings=Count('rawfinding')).distinct()
    print (dataset_7days)

    # Dataset 2: security fix applied 7 days max, CVSS >= 7.0, reboot required
    ness_pluginid_reboot = [
        35453, # Microsoft Windows Update Reboot Required - http://www.tenable.com/plugins/index.php?view=single&id=35453
        63756, # AIX 5.2 TL 0 : reboot - http://www.tenable.com/plugins/index.php?view=single&id=63756
        63757, # AIX 5.3 TL 0 : reboot http://www.tenable.com/plugins/index.php?view=single&id=63757
    ]
    dataset_7days_reboot = Asset.objects.for_user(request.user).filter(
        Q(rawfinding__created_at__gt=seven_days_ago) &
        Q(rawfinding__risk_info__vuln_publication_date__gte=seven_days_ago.strftime('%Y/%m/%d')) &
        Q(rawfinding__risk_info__cvss_base_score__gte=7.0) &
        Q(rawfinding__type__in=ness_plugin_family_osupdates) &
        Q(rawfinding__raw_data__plugin_information__plugin_id__in=ness_pluginid_reboot) # reboot needed
        #Q(rawfinding__scan__engine_policy_id=1)
    ).distinct()

    # Dataset 3: 30 days ago
    dataset_30days = Asset.objects.for_user(request.user).filter(
        Q(rawfinding__created_at__gt=month_ago) &
        Q(rawfinding__risk_info__vuln_publication_date__gte=month_ago.strftime('%Y/%m/%d')) &
        Q(rawfinding__risk_info__cvss_base_score__gte=7.0) &
        Q(rawfinding__type__in=ness_plugin_family_osupdates)
        #Q(rawfinding__scan__engine_policy_id=1)
    ).distinct()

    # Dataset 4: more than 30 missing patches (CVSS >= 7.0)
    dataset_30missing = Asset.objects.for_user(request.user).filter(
        #Q(rawfinding__created_at__gt=month_ago) &
        Q(rawfinding__risk_info__vuln_publication_date__gte=month_ago.strftime('%Y/%m/%d')) &
        Q(rawfinding__risk_info__cvss_base_score__gte=7.0) &
        Q(rawfinding__type__in=ness_plugin_family_osupdates)
        #Q(rawfinding__scan__engine_policy_id=1)
    ).annotate(num_missing_patches=Count('rawfinding')).filter(num_missing_patches__gte=30).distinct()

    data.append({
        "7days": dataset_7days.count(),
        "7days_reboot": dataset_7days_reboot.count(),
        "30days": dataset_30days.count(),
        "30missing": dataset_30missing.count(),
    })

    return render(request, 'patch-management-dashboard.html', {'data': data})
