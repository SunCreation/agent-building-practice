"""Mechanics use synthetic files and never embed upstream answers."""
import hashlib
from io import BytesIO
from unittest.mock import patch
import pytest
from harness_lab import benchmark_source as source


def files():
    return {'task.toml':b'[metadata]\ndifficulty="easy"\ncategory="data-processing"\n','instruction.md':b'Read /protected/papers; write /app/result.json','environment/papers/paper.txt':b'input data','tests/test_outputs.py':b'PRIVATE_VERIFIER','solution/solve.sh':b'PRIVATE_SOLUTION'}


def manifest(data):
    return {'repo':'alibaba/terminal-bench-pro','revision':'a'*40,'tasks':[{'name':'extract-paper-metadata-to-json','difficulty':'easy','category':'data-processing','source_files':[{'path':p,'sha256':hashlib.sha256(b).hexdigest(),'size_bytes':len(b)} for p,b in data.items()]}]}


def cache_write(cache,data):
    for name,content in data.items():
        path=cache/'extract-paper-metadata-to-json'/name
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)


def test_copy_only_fixtures(tmp_path):
    data=files();cache=tmp_path/'cache';cache_write(cache,data);target=tmp_path/'trial'
    with patch.object(source,'_manifest',return_value=manifest(data)),patch.object(source,'urlopen',side_effect=AssertionError('Unexpected network')):
        result=source.prepare('extract-paper-metadata-to-json',target,cache)
    assert (target/'protected/papers/paper.txt').read_bytes()==b'input data'
    assert not (target/'tests').exists() and not (target/'solution').exists()
    assert result['cwd']==target/'app'
    assert result['instruction']==f'Read {target}/protected/papers; write {target}/app/result.json'
    assert len(result['fixture_sha256'])==1


def test_cache_tampering(tmp_path):
    data=files();cache=tmp_path/'cache';cache_write(cache,data)
    (cache/'extract-paper-metadata-to-json/tests/test_outputs.py').write_bytes(b'tampered')
    with patch.object(source,'_manifest',return_value=manifest(data)):
        with pytest.raises(ValueError,match='SHA256'):
            source.prepare('extract-paper-metadata-to-json',tmp_path/'trial',cache)
    assert not (tmp_path/'trial').exists()


def test_bad_download_not_cached(tmp_path):
    with patch.object(source,'_manifest',return_value=manifest(files())),patch.object(source,'urlopen',return_value=BytesIO(b'bad')):
        with pytest.raises(ValueError,match='SHA256'):source.ensure_sources(tmp_path/'cache')
    assert not (tmp_path/'cache/extract-paper-metadata-to-json/task.toml').exists()


def test_fixed_commit_download_once(tmp_path):
    data=files();seen=[]
    def download(url,timeout):
        seen.append(url)
        prefix='https://raw.githubusercontent.com/alibaba/terminal-bench-pro/'+'a'*40+'/extract-paper-metadata-to-json/'
        assert url.startswith(prefix)
        return BytesIO(data[url[len(prefix):]])
    with patch.object(source,'_manifest',return_value=manifest(data)),patch.object(source,'urlopen',side_effect=download):
        cache=source.ensure_sources(tmp_path/'cache');source.ensure_sources(cache)
    assert len(seen)==len(data)


def test_relocate_once(tmp_path):
    root=tmp_path/'tmp'/'workspace'
    text='/app/a /protected/p /db/d /home/user/a /workspace/b /tmp/c /application /tmpfile https://example/app'
    expected=' '.join([str(root)+p for p in ['/app/a','/protected/p','/db/d','/home/user/a','/workspace/b','/tmp/c']])+' /application /tmpfile https://example/app'
    assert source.relocate(text,root)==expected


def test_existing_target_preserved(tmp_path):
    data=files();cache=tmp_path/'cache';cache_write(cache,data);target=tmp_path/'trial';target.mkdir();(target/'keep').write_text('keep')
    with patch.object(source,'_manifest',return_value=manifest(data)):
        with pytest.raises(ValueError,match='new or empty'):source.prepare('extract-paper-metadata-to-json',target,cache)
    assert (target/'keep').read_text()=='keep'


def test_cache_symlink_rejected(tmp_path):
    outside=tmp_path/'outside';outside.mkdir();cache=tmp_path/'cache';cache.mkdir();(cache/'escape').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='escapes'):source._safe_file(cache,'escape/file')


def test_unlisted_cache_file_not_exposed(tmp_path):
    data=files();cache=tmp_path/'cache';cache_write(cache,data)
    (cache/'extract-paper-metadata-to-json/environment/papers/extra.txt').write_text('not pinned')
    with patch.object(source,'_manifest',return_value=manifest(data)):
        source.prepare('extract-paper-metadata-to-json',tmp_path/'trial',cache)
    assert not (tmp_path/'trial/protected/papers/extra.txt').exists()


def test_recover_expected_answer_stays_evaluator_only(tmp_path):
    name='recover-encrypted-db-credentials'
    data={
        'task.toml':b'[metadata]\ndifficulty="medium"\ncategory="data-processing"\n',
        'instruction.md':b'Recover data in /db',
        'environment/test_data/expected_keys.txt':b'evaluator-only-answer',
        'environment/test_data/notes.txt':b'public fixture',
        'environment/setup.sh':b"python3 << 'PYTHON_SETUP'\nfrom pathlib import Path\nPath('/db/input.txt').write_text('generated input')\nPYTHON_SETUP\n",
    }
    spec=manifest(data);spec['tasks'][0]['name']=name;spec['tasks'][0]['difficulty']='medium'
    cache=tmp_path/'cache'
    for path,content in data.items():
        dest=cache/name/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(content)
    with patch.object(source,'_manifest',return_value=spec):
        result=source.prepare(name,tmp_path/'trial',cache)
    assert not (tmp_path/'trial/protected/test_data/expected_keys.txt').exists()
    assert (tmp_path/'trial/protected/test_data/notes.txt').read_bytes()==b'public fixture'
    assert (tmp_path/'trial/db/input.txt').read_text()=='generated input'
    assert (result['task_dir']/'environment/test_data/expected_keys.txt').read_bytes()==b'evaluator-only-answer'
    assert all('expected_keys' not in p for p in result['fixture_sha256'])


def test_prepare_flag_without_name_fetches_all(tmp_path, capsys):
    with patch.object(source,'ensure_sources',return_value=tmp_path) as fetch:
        assert source.main(['--prepare','--cache',str(tmp_path)])==0
    fetch.assert_called_once_with(tmp_path)
    assert 'cache' in capsys.readouterr().out
