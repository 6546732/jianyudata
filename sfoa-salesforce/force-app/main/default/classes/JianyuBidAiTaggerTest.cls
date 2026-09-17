@IsTest
private class JianyuBidAiTaggerTest {
    private class MockDeepSeek implements HttpCalloutMock {
        public HttpResponse respond(HttpRequest request) {
            System.assert(request.getEndpoint().contains('DeepSeek_Core_Business'));
            HttpResponse response = new HttpResponse();
            response.setStatusCode(200);
            response.setBody(JSON.serialize(new Map<String, Object>{
                'status' => 'completed',
                'output_text' => '{"matched":true,"confidence":0.85,"primary_industry":"半导体","reason":"采购芯片设备"}'
            }));
            return response;
        }
    }

    @IsTest
    static void evaluatesRuleAndWritesAuditableAssignment() {
        Tag__c tag = new Tag__c(
            Name = '测试半导体标讯', Tag_Category__c = 'biaoxun', Is_Active__c = true
        );
        insert tag;
        Tag_Rule__c rule = new Tag_Rule__c(
            Name = '测试规则', Tag__c = tag.Id, Is_Active__c = true
        );
        insert rule;
        insert new Tag_Rule_Item__c(
            Tag_Rule__c = rule.Id, AI_Semantics__c = '是否采购半导体设备', Is_Active__c = true
        );
        bidnews__c bid = new bidnews__c(
            Name = '测试芯片设备采购',
            Source_Content__c = '采购芯片设备', Source_Industry__c = '信息技术'
        );
        insert bid;
        System.assertEquals('待识别',
            [SELECT AI_Status__c FROM bidnews__c WHERE Id = :bid.Id].AI_Status__c);
        Test.setMock(HttpCalloutMock.class, new MockDeepSeek());
        Test.startTest();
        JianyuBidAiTagger.enqueue(bid.Id);
        Test.stopTest();
        bid = [SELECT AI_Status__c, Primary_Industry__c, AI_Confidence__c
               FROM bidnews__c WHERE Id = :bid.Id];
        System.assertEquals('已识别', bid.AI_Status__c);
        System.assertEquals('半导体', bid.Primary_Industry__c);
        System.assertEquals(Decimal.valueOf('0.85'), bid.AI_Confidence__c);
        List<Bid_Tag_Assignment__c> items = [
            SELECT Is_Matched__c, Tag__c, Reason__c
            FROM Bid_Tag_Assignment__c WHERE Bid_News__c = :bid.Id
        ];
        System.assertEquals(1, items.size());
        System.assertEquals(true, items[0].Is_Matched__c);
        System.assertEquals(tag.Id, items[0].Tag__c);
    }

    @IsTest
    static void parsesTopLevelOutputText() {
        String body = JSON.serialize(new Map<String, Object>{
            'status' => 'completed',
            'output_text' => '{"matched":true,"confidence":0.85,"primary_industry":"半导体","reason":"采购芯片"}'
        });
        Map<String, Object> verdict = JianyuBidAiTagger.parseVerdict(body);
        System.assertEquals(true, verdict.get('matched'));
        System.assertEquals('半导体', verdict.get('primary_industry'));
    }

    @IsTest
    static void parsesNestedMessageAndRejectsIncomplete() {
        String body = JSON.serialize(new Map<String, Object>{
            'status' => 'completed',
            'output' => new List<Object>{new Map<String, Object>{
                'type' => 'message',
                'content' => new List<Object>{new Map<String, Object>{
                    'type' => 'output_text',
                    'text' => '{"matched":false,"confidence":0.2,"primary_industry":"信息技术","reason":"不是半导体项目"}'
                }}
            }}
        });
        Map<String, Object> verdict = JianyuBidAiTagger.parseVerdict(body);
        System.assertEquals(false, verdict.get('matched'));
        try {
            JianyuBidAiTagger.parseVerdict('{"status":"in_progress"}');
            System.assert(false, '未完成响应应被拒绝');
        } catch (JianyuBidAiTagger.TaggingException expected) {
            System.assert(expected.getMessage().contains('未完成'));
        }
    }
}
