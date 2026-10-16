import pytest

from Sprout.skills.models import Skill
from Sprout.skills.repository import SkillRepository


@pytest.mark.parametrize("instructions", [
    "Inspect errors before retrying. ",
    "First line\nSecond line\n",
    'Preserve triple quotes: """ and backslashes: C:\\work\\repo',
    "中文说明\r\n保留换行\t与缩进",
])
def test_published_instructions_roundtrip_exactly(tmp_path, instructions):
    skill = Skill(name="roundtrip", version="1.0", instructions=instructions,
                  required_tools=('tool"quoted', "path\\tool"))
    repository = SkillRepository(tmp_path)
    repository.save(skill)
    loaded = repository.load(skill.name)
    assert loaded.instructions == skill.instructions
    assert loaded.version == skill.version
    assert loaded.required_tools == skill.required_tools
