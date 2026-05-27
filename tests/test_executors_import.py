def test_executors_import_without_qt():
    from scanflow.automation.executors import (
        DriftExecutor,
        ExecutorContext,
        MosaicExecutor,
        ScanExecutor,
        SurveyExecutor,
    )

    assert ExecutorContext is not None
    assert ScanExecutor is not None
    assert DriftExecutor is not None
    assert SurveyExecutor is not None
    assert MosaicExecutor is not None
