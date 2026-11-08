"""制作写操作共用的准确选题状态守卫，不拥有事务提交。"""

from agent4pharma.modules.editorial.commission_contracts import Story
from agent4pharma.runtime.production_execution import ProductionTask
from agent4pharma.workflows.production_dependencies import ProductionUnit


def active_story(work: ProductionUnit, task: ProductionTask) -> Story:
    """编辑和提交均校验任务书/取消状态及并发版本，拒绝过期模型结果。"""
    story = work.stories.get(task.story_id)
    if story.status != "active" or story.revision != task.expected_revision:
        raise ValueError("选题已暂停、取消或版本变化，请重新建立制作任务")
    return story
