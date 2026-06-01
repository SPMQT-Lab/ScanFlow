def test_executors_import_without_qt():
    from scanflow.automation.executors import (
        ExecutorContext,
        MosaicExecutor,
        ScanExecutor,
        SurveyExecutor,
    )

    assert ExecutorContext is not None
    assert ScanExecutor is not None
    assert SurveyExecutor is not None
    assert MosaicExecutor is not None
