from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET

from scripts import update_stats as stats

NOW = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)


def repository(index, **changes):
    repo = {'id':index,'name':f'project-{index}','owner':{'login':'naab-hub'},
            'private':False,'visibility':'public','fork':False,'language':'C',
            'stargazers_count':2,'forks_count':1,'pushed_at':'2026-11-10T10:00:00Z'}
    repo.update(changes)
    return repo


class RefreshTests(unittest.TestCase):
    def test_user_endpoint_and_pagination(self):
        first = [repository(i) for i in range(95)] + [repository(95,private=True),
            repository(96,fork=True), repository(97,name='NAAB-HUB'),
            repository(98,visibility='internal'),repository(99,owner={'login':'other'})]
        def fetch(path):
            if path == '/users/naab-hub':
                return {'login':'naab-hub','type':'User'}
            self.assertEqual(urlsplit(path).path,'/users/naab-hub/repos')
            query = parse_qs(urlsplit(path).query)
            self.assertEqual(query['type'],['owner'])
            return first if query['page']==['1'] else [repository(100),repository(1)]
        self.assertEqual(len(stats.public_projects('naab-hub',fetch)),96)

    def test_empty_account(self):
        def fetch(path):
            return [] if '/repos?' in path else {'login':'naab-hub','type':'User'}
        self.assertEqual(stats.public_projects('naab-hub',fetch),[])
        snapshot = stats.summarize([],NOW)
        self.assertEqual(snapshot.projects,0)
        self.assertIn('No public source projects yet',stats.dashboard(snapshot,stats.DESKTOP))

    def test_wrong_account_fails(self):
        with self.assertRaises(ValueError):
            stats.public_projects('naab-hub',lambda path:{'login':'naab-hub','type':'Organization'})

    def test_api_failure_propagates(self):
        def fail(path):
            raise OSError('unavailable')
        with self.assertRaises(OSError):
            stats.public_projects('naab-hub',fail)

    def test_months_and_recent(self):
        snapshot = stats.summarize([repository(1),repository(2,pushed_at='2026-06-01T00:00:00Z'),
            repository(3,pushed_at=None,language=None),repository(4,pushed_at='2026-05-01T00:00:00Z')],NOW)
        self.assertEqual((snapshot.projects,snapshot.stars,snapshot.forks,snapshot.recent),(4,8,4,1))
        self.assertEqual(snapshot.months,(('Jun',1),('Jul',0),('Aug',0),('Sep',0),('Oct',0),('Nov',1)))
        self.assertEqual(snapshot.older,2)
        self.assertEqual(sum(count for _,count in snapshot.languages),4)

    def test_year_boundary(self):
        self.assertEqual(stats.last_six_months(datetime(2026,1,1,tzinfo=timezone.utc)),
            [(2025,8),(2025,9),(2025,10),(2025,11),(2025,12),(2026,1)])

    def test_language_aggregation_and_xml(self):
        projects = [repository(i,language=name) for i,name in enumerate(['A&B','C','C++','MATLAB','Python','TypeScript'])]
        snapshot = stats.summarize(projects,NOW)
        self.assertEqual(len(snapshot.languages),5)
        self.assertEqual(sum(count for _,count in snapshot.languages),6)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            stats.write_cards(output,snapshot)
            for name in ('naab-activity.svg','naab-activity-mobile.svg'):
                ET.parse(output/name)
                self.assertIn('2026-11-28 12:00 UTC',(output/name).read_text())
                self.assertNotIn('FIRST REFRESH PENDING',(output/name).read_text())


if __name__ == '__main__':
    unittest.main()
